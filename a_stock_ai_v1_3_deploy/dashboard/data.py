from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.config import settings
from learning.strategy_params import PARAMS_FILE, PARAM_SPECS, default_strategy_params
from scheduler.trading_calendar import BEIJING_TZ, current_trading_window
from simulation.paper_trading import current_paper_account


ETF_PAPER_ACCOUNT_ID = "etf_minute_paper"


def build_dashboard_payload() -> dict[str, Any]:
    latest_cycle = _latest_cycle()
    latest_payload = _loads(latest_cycle.get("payload_json")) if latest_cycle else {}
    final_signal = _dict(latest_payload.get("final_signal"))
    state = _dict(latest_payload.get("state"))
    sentiment = _dict(latest_payload.get("market_sentiment") or final_signal.get("market_sentiment"))
    leader = _dict(latest_payload.get("leader"))
    push = _dict(latest_payload.get("push"))
    multi_strategy = _dict(latest_payload.get("multi_strategy"))
    primary_strategy = _dict(latest_payload.get("primary_strategy"))
    recommendations = _list(final_signal.get("recommendations"))
    watchlist = _list(final_signal.get("watchlist"))
    paper_account = _paper_account_payload()
    paper_account_id = str(paper_account.get("account_id") or "")

    return {
        "generated_at": _now(),
        "read_only": True,
        "no_real_orders": True,
        "engine": _engine_payload(),
        "health": _health_payload(latest_cycle),
        "latest_cycle": _cycle_summary(latest_cycle, latest_payload, final_signal),
        "signal": _signal_payload(final_signal),
        "state": state,
        "sentiment": sentiment,
        "leader": leader,
        "push": push,
        "recommendations": recommendations,
        "watchlist": watchlist,
        "paper_account": paper_account,
        "paper_simulation_health": _paper_simulation_health(paper_account),
        "recent_orders": _recent_orders(paper_account_id),
        "recent_trades": _recent_trades(paper_account_id),
        "paper_equity_curve": _paper_equity_curve(paper_account_id),
        "ai_execution_health": _ai_execution_health(),
        "recent_cycles": _recent_cycles(),
        "strategy": _strategy_payload(),
        "multi_strategy": _multi_strategy_payload(multi_strategy),
        "primary_strategy": primary_strategy,
        "backtests": _backtest_payload(),
    }


def build_etf_dashboard_payload() -> dict[str, Any]:
    """Build a read-only ETF minute-strategy view from its isolated SQLite store."""
    db_path = _etf_db_path()
    latest_row = _etf_latest_decision(db_path)
    plan = _loads(latest_row.get("payload_json")) if latest_row else {}
    target = _dict(plan.get("target"))
    confirmation = _dict(plan.get("intraday_confirmation"))
    execution = _dict(plan.get("minute_execution"))
    account, positions = _etf_account_payload(db_path)
    candidates = [item for item in _list(plan.get("candidates")) if isinstance(item, dict)]
    data_freshness = _etf_data_freshness(db_path)

    return {
        "generated_at": _now(),
        "read_only": True,
        "no_real_orders": True,
        "strategy": {
            "strategy_id": plan.get("strategy_id", "wufu_etf"),
            "strategy_version": plan.get("strategy_version", "--"),
            "execution_mode": plan.get("execution_mode", "etf_minute_paper_trading_only"),
            "as_of": plan.get("as_of"),
            "regime": plan.get("regime", "--"),
            "regime_detail": _dict(plan.get("regime_detail")),
            "reasoning": plan.get("reasoning", "等待ETF分钟策略决策"),
            "risk_flags": _list(plan.get("risk_flags")),
        },
        "health": _etf_health_payload(db_path, latest_row, data_freshness),
        "data_freshness": data_freshness,
        "latest_decision": {
            "decision_id": latest_row.get("decision_id") if latest_row else None,
            "created_at": latest_row.get("created_at") if latest_row else None,
            "signal": plan.get("signal", "HOLD"),
            "target_weight": _float(plan.get("target_weight"), 0.0),
            "target": target,
            "trade_plan": [item for item in _list(plan.get("trade_plan")) if isinstance(item, dict)],
            "confirmation": confirmation,
            "execution": execution,
            "ai_review": _dict(plan.get("ai_review")),
        },
        "account": account,
        "positions": positions,
        "candidates": candidates,
        "recent_orders": _etf_recent_orders(db_path),
        "recent_decisions": _etf_recent_decisions(db_path),
    }


def latest_backtest_chart_path() -> Path | None:
    for item in _backtest_payload().get("runs", []):
        chart_path = item.get("chart_path")
        if chart_path:
            path = _safe_storage_path(chart_path)
            if path and path.exists() and path.suffix.lower() == ".svg":
                return path

    storage = settings.storage_dir
    candidates = sorted(storage.glob("**/*hybrid_alpha.svg"), key=lambda item: item.stat().st_mtime, reverse=True)
    candidates.extend(sorted(storage.glob("historical_backtest_*.svg"), key=lambda item: item.stat().st_mtime, reverse=True))
    for path in candidates:
        safe = _safe_storage_path(path)
        if safe and safe.exists() and safe.suffix.lower() == ".svg":
            return safe
    return None


def _engine_payload() -> dict[str, Any]:
    return {
        "name": settings.engine_name,
        "positioning": settings.engine_positioning,
        "value_statement": settings.value_statement,
        "loop_seconds": settings.loop_seconds,
        "paper_trading_enabled": settings.paper_trading_enabled,
        "dry_run": settings.dry_run,
        "max_push_stocks": settings.max_push_stocks,
        "max_position_weight": settings.max_position_weight,
        "timezone": BEIJING_TZ.key,
        "dashboard_version": "v1.9",
        "multi_strategy_enabled": settings.multi_strategy_enabled,
        "multi_strategy_mode": settings.multi_strategy_mode,
        "multi_strategy_paper_enabled": settings.multi_strategy_paper_enabled,
        "primary_strategy_id": settings.primary_strategy_id,
        "hybrid_alpha_lock_parameters": settings.hybrid_alpha_lock_parameters,
    }


def _health_payload(latest_cycle: dict[str, Any] | None) -> dict[str, Any]:
    window = current_trading_window()
    if not settings.db_path.exists():
        return {
            "ok": False,
            "status": "missing_db",
            "message": "SQLite 日志文件不存在",
            "market_window": window.__dict__,
        }
    if latest_cycle is None:
        return {
            "ok": not window.is_open,
            "status": "no_cycle",
            "message": "暂无运行周期日志",
            "market_window": window.__dict__,
        }

    status = str(latest_cycle.get("status") or "unknown")
    age_seconds = _age_seconds(latest_cycle.get("started_at"))
    stale_limit = max(settings.loop_seconds * 3 + 60, 300)
    is_stale = window.is_open and age_seconds is not None and age_seconds > stale_limit
    ok = status != "failed" and not is_stale
    message = "运行正常"
    if status == "failed":
        message = "最新周期失败"
    elif is_stale:
        message = f"最新周期已超过 {int(age_seconds or 0)} 秒未更新"
    elif not window.is_open:
        message = f"当前非交易时段：{window.phase}"

    return {
        "ok": ok,
        "status": "ok" if ok else "attention",
        "message": message,
        "latest_cycle_age_seconds": age_seconds,
        "stale_limit_seconds": stale_limit,
        "market_window": window.__dict__,
    }


def _cycle_summary(
    row: dict[str, Any] | None,
    payload: dict[str, Any],
    final_signal: dict[str, Any],
) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "cycle_id": row.get("cycle_id"),
        "trigger_source": row.get("trigger_source"),
        "status": row.get("status"),
        "market_phase": row.get("market_phase"),
        "quote_count": row.get("quote_count"),
        "leader_count": row.get("leader_count"),
        "decision_count": row.get("decision_count"),
        "push_status": row.get("push_status"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "signal": final_signal.get("signal"),
        "recommendation_count": final_signal.get("recommendation_count", len(_list(final_signal.get("recommendations")))),
        "quotes_count": payload.get("quotes_count"),
        "selection_universe": _dict(payload.get("selection_universe")),
    }


def _signal_payload(final_signal: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal": final_signal.get("signal", "HOLD"),
        "position": _float(final_signal.get("position"), 0.0),
        "risk_level": final_signal.get("risk_level", "normal"),
        "reasoning": final_signal.get("reasoning", ""),
        "ai_provider": final_signal.get("ai_provider"),
        "ai_error": final_signal.get("ai_error"),
        "ai_degraded": bool(final_signal.get("ai_degraded")),
        "ai_execution_mode": final_signal.get("ai_execution_mode"),
        "ai_execution_chain": final_signal.get("ai_execution_chain"),
        "selection_logic": final_signal.get("selection_logic"),
        "market_stage": final_signal.get("market_stage"),
        "market_stage_reason": final_signal.get("market_stage_reason"),
        "no_recommendation_reason": final_signal.get("no_recommendation_reason"),
        "risk_flags": _list(final_signal.get("risk_flags")),
        "strategy_id": final_signal.get("strategy_id"),
        "strategy_label": final_signal.get("strategy_label"),
        "strategy_version": final_signal.get("strategy_version"),
    }


def _paper_account_payload() -> dict[str, Any]:
    try:
        return current_paper_account()
    except Exception as exc:
        return {"error": str(exc), "positions": {}, "equity": 0.0, "cash": 0.0, "market_value": 0.0}


def _recent_orders(account_id: str, limit: int = 40) -> list[dict[str, Any]]:
    if not account_id:
        return []
    rows = _query(
        """
        SELECT cycle_id, symbol, name, side, status, quantity, price, reason, created_at
        FROM paper_orders
        WHERE account_id = ? AND status != 'skipped'
        ORDER BY id DESC
        LIMIT ?
        """,
        (account_id, limit),
    )
    return rows


def _recent_trades(account_id: str, limit: int = 30) -> list[dict[str, Any]]:
    if not account_id:
        return []
    rows = _query(
        """
        SELECT cycle_id, symbol, name, side, quantity, price, amount, fee, tax, slippage, realized_pnl, created_at
        FROM paper_trades
        WHERE account_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (account_id, limit),
    )
    return rows


def _paper_equity_curve(account_id: str, limit: int = 120) -> list[dict[str, Any]]:
    if not account_id:
        return []
    rows = _query(
        """
        SELECT created_at, cash, equity, market_value, realized_pnl, unrealized_pnl, status
        FROM paper_account_snapshots
        WHERE account_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (account_id, limit),
    )
    return list(reversed(rows))


def _paper_simulation_health(account: dict[str, Any]) -> dict[str, Any]:
    metrics = _dict(account.get("session_metrics"))
    completed = int(metrics.get("sell_trade_count") or 0)
    filled = int(metrics.get("filled_order_count") or 0)
    rejected = int(metrics.get("rejected_order_count") or 0)
    reliable = completed >= 20
    if completed == 0:
        message = "当前账户尚无已完成平仓交易，不能与年度回测比较。"
    elif not reliable:
        message = f"当前账户仅有 {completed} 笔已完成平仓交易，样本仍不足。"
    else:
        message = "当前账户已有基础样本，可作为实时执行表现的阶段性观察。"
    return {
        "account_id": account.get("account_id"),
        "session_started_at": account.get("session_started_at"),
        "completed_trade_count": completed,
        "filled_order_count": filled,
        "rejected_order_count": rejected,
        "sufficient_for_backtest_comparison": reliable,
        "message": message,
    }


def _ai_execution_health(limit: int = 360) -> dict[str, Any]:
    rows = _query(
        """
        SELECT payload_json
        FROM cycle_logs
        WHERE status = 'ok'
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    observed = 0
    degraded = 0
    for row in rows:
        payload = _loads(row.get("payload_json"))
        final_signal = _dict(payload.get("final_signal"))
        if not final_signal:
            continue
        observed += 1
        if bool(final_signal.get("ai_degraded")):
            degraded += 1
    return {
        "observed_cycles": observed,
        "degraded_cycles": degraded,
        "degraded_rate": round(degraded / observed, 4) if observed else 0.0,
        "status": "attention" if observed and degraded / observed >= 0.05 else "ok",
    }


def _recent_cycles(limit: int = 36) -> list[dict[str, Any]]:
    rows = _query(
        """
        SELECT cycle_id, trigger_source, status, quote_count, decision_count, push_status, payload_json, started_at, finished_at
        FROM cycle_logs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    summaries = []
    for row in rows:
        payload = _loads(row.pop("payload_json", None))
        final_signal = _dict(payload.get("final_signal"))
        summaries.append(
            {
                **row,
                "signal": final_signal.get("signal", "HOLD"),
                "risk_level": final_signal.get("risk_level"),
                "recommendation_count": final_signal.get("recommendation_count", 0),
                "market_stage": final_signal.get("market_stage"),
            }
        )
    return summaries


def _strategy_payload() -> dict[str, Any]:
    document = _load_strategy_document()
    latest_learning = _query(
        """
        SELECT run_id, trigger_source, status, sample_count, evaluated_count, metrics_json,
               proposal_json, applied_json, error_text, created_at
        FROM learning_runs
        ORDER BY id DESC
        LIMIT 1
        """
    )
    param_versions = _query(
        """
        SELECT version, status, reason, changes_json, metrics_json, created_at
        FROM strategy_param_versions
        ORDER BY id DESC
        LIMIT 5
        """
    )
    return {
        "version": document.get("version", 1),
        "updated_at": document.get("updated_at"),
        "source": document.get("source", "env_defaults"),
        "reason": document.get("reason"),
        "last_self_learning_at": document.get("last_self_learning_at"),
        "params": document.get("params") or default_strategy_params(),
        "bounds": document.get("bounds") or _param_bounds(),
        "latest_learning": _decode_json_columns(latest_learning[0]) if latest_learning else None,
        "param_versions": [_decode_json_columns(row) for row in param_versions],
        "rules": [
            "市场风格先决定稳健、成长、热点三类策略的资金预算",
            "涨停、接近涨停、ST、停牌与低流动性标的不能进入可买入池",
            "优质成长策略必须取得新鲜 TuShare 财务数据，否则只观察",
            "MiniMax 只确认系统候选池与组合计划，不能凭空新增股票",
            "模拟盘执行 T+1、100 股一手、滑点、佣金、印花税和目标仓位约束",
        ],
    }


def _multi_strategy_payload(plan: dict[str, Any]) -> dict[str, Any]:
    portfolio = _dict(plan.get("portfolio_signal"))
    allocation = _dict(plan.get("allocation"))
    regime = _dict(plan.get("regime"))
    history = _query(
        """
        SELECT strategy_id, strategy_label, regime, mode, budget, target_exposure,
               candidate_count, recommendation_count, status, reason, created_at
        FROM strategy_cycle_logs
        ORDER BY id DESC
        LIMIT 12
        """
    )
    return {
        "version": plan.get("version", "v1.9"),
        "mode": plan.get("mode", settings.multi_strategy_mode),
        "generated_for": plan.get("generated_for"),
        "regime": regime,
        "allocation": allocation,
        "strategies": _list(plan.get("strategies")),
        "portfolio": portfolio,
        "recent_history": history,
    }


def _backtest_payload() -> dict[str, Any]:
    rows = _query(
        """
        SELECT run_id, strategy_name, status, lookback_days, holding_days, trade_count,
               win_rate, total_return_pct, max_drawdown_pct, metrics_json, created_at
        FROM backtest_runs
        ORDER BY id DESC
        LIMIT 20
        """
    )
    runs = []
    for row in rows:
        metrics = _loads(row.get("metrics_json"))
        row.pop("metrics_json", None)
        runs.append(
            {
                **row,
                "metrics": metrics,
                "chart_path": metrics.get("chart_path"),
                "result_path": metrics.get("result_path"),
            }
        )
    chart = latest_backtest_chart_path_from_runs(runs)
    return {
        "runs": runs,
        "latest_chart_available": bool(chart),
        "latest_chart_name": chart.name if chart else None,
    }


def latest_backtest_chart_path_from_runs(runs: list[dict[str, Any]]) -> Path | None:
    for item in runs:
        chart_path = item.get("chart_path")
        if not chart_path:
            continue
        path = _safe_storage_path(chart_path)
        if path and path.exists() and path.suffix.lower() == ".svg":
            return path
    return None


def _latest_cycle() -> dict[str, Any] | None:
    rows = _query(
        """
        SELECT *
        FROM cycle_logs
        ORDER BY id DESC
        LIMIT 1
        """
    )
    return rows[0] if rows else None


def _query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return _query_path(settings.db_path, sql, params)


def _query_path(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    uri = f"file:{quote(str(db_path), safe='/')}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


def _etf_db_path() -> Path:
    configured = os.getenv("ETF_STRATEGY_DB_PATH")
    return Path(configured or settings.project_root / "etf_strategy" / "local_data" / "etf_strategy.sqlite").resolve()


def _etf_query(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return _query_path(db_path, sql, params)


def _etf_latest_decision(db_path: Path) -> dict[str, Any] | None:
    rows = _etf_query(
        db_path,
        """
        SELECT decision_id, as_of, strategy_id, signal, target_symbol, payload_json, created_at
        FROM etf_decisions
        ORDER BY created_at DESC
        LIMIT 1
        """,
    )
    return rows[0] if rows else None


def _etf_account_payload(db_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    account_rows = _etf_query(
        db_path,
        """
        SELECT account_id, cash, initial_cash, realized_pnl, updated_at
        FROM etf_paper_accounts
        WHERE account_id = ?
        LIMIT 1
        """,
        (ETF_PAPER_ACCOUNT_ID,),
    )
    position_rows = _etf_query(
        db_path,
        """
        SELECT symbol, name, quantity, available_quantity, avg_cost, last_price, last_trade_date, updated_at
        FROM etf_paper_positions
        WHERE account_id = ?
        ORDER BY symbol
        """,
        (ETF_PAPER_ACCOUNT_ID,),
    )
    positions = []
    for row in position_rows:
        quantity = _float(row.get("quantity"), 0.0)
        avg_cost = _float(row.get("avg_cost"), 0.0)
        last_price = _float(row.get("last_price"), 0.0)
        market_value = quantity * last_price
        cost_value = quantity * avg_cost
        positions.append(
            {
                **row,
                "market_value": round(market_value, 2),
                "unrealized_pnl": round(market_value - cost_value, 2),
                "unrealized_return_pct": round((last_price / avg_cost - 1.0) * 100, 4) if avg_cost > 0 else 0.0,
            }
        )

    row = account_rows[0] if account_rows else {}
    cash = _float(row.get("cash"), 0.0)
    initial_cash = _float(row.get("initial_cash"), 0.0)
    market_value = sum(_float(item.get("market_value"), 0.0) for item in positions)
    equity = cash + market_value
    return (
        {
            "account_id": row.get("account_id", ETF_PAPER_ACCOUNT_ID),
            "cash": round(cash, 2),
            "initial_cash": round(initial_cash, 2),
            "realized_pnl": round(_float(row.get("realized_pnl"), 0.0), 2),
            "market_value": round(market_value, 2),
            "equity": round(equity, 2),
            "return_pct": round((equity / initial_cash - 1.0) * 100, 4) if initial_cash > 0 else 0.0,
            "updated_at": row.get("updated_at"),
        },
        positions,
    )


def _etf_recent_orders(db_path: Path, limit: int = 36) -> list[dict[str, Any]]:
    return _etf_query(
        db_path,
        """
        SELECT order_id, side, symbol, name, status, quantity, price, commission, reason, created_at
        FROM etf_paper_orders
        WHERE account_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (ETF_PAPER_ACCOUNT_ID, limit),
    )


def _etf_recent_decisions(db_path: Path, limit: int = 20) -> list[dict[str, Any]]:
    rows = _etf_query(
        db_path,
        """
        SELECT decision_id, as_of, strategy_id, signal, target_symbol, payload_json, created_at
        FROM etf_decisions
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    summaries = []
    for row in rows:
        plan = _loads(row.pop("payload_json", None))
        execution = _dict(plan.get("minute_execution"))
        summaries.append(
            {
                **row,
                "target_name": _dict(plan.get("target")).get("name"),
                "target_weight": _float(plan.get("target_weight"), 0.0),
                "regime": plan.get("regime"),
                "execution_status": execution.get("status"),
                "execution_reason": execution.get("reason"),
            }
        )
    return summaries


def _etf_health_payload(
    db_path: Path,
    latest_row: dict[str, Any] | None,
    data_freshness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    window = current_trading_window()
    if not db_path.exists():
        return {
            "ok": False,
            "status": "missing_db",
            "message": "ETF策略数据库不存在",
            "market_window": window.__dict__,
        }
    if latest_row is None:
        return {
            "ok": not window.is_open,
            "status": "no_decision",
            "message": "暂无ETF分钟策略决策",
            "market_window": window.__dict__,
        }

    age_seconds = _age_seconds(latest_row.get("created_at"))
    stale_limit = 300
    minute_age_seconds = _age_seconds(_dict(data_freshness).get("latest_minute_time"))
    stale = window.is_open and (
        (age_seconds is not None and age_seconds > stale_limit)
        or (minute_age_seconds is not None and minute_age_seconds > stale_limit)
    )
    if not window.is_open:
        latest_minute = _dict(data_freshness).get("latest_minute_time") or "无分钟数据"
        message = f"当前非交易时段；最近ETF分钟数据：{latest_minute}"
    elif stale:
        message = "ETF决策或分钟行情已超过允许时效，暂停新的分钟执行。"
    else:
        message = "运行正常"
    return {
        "ok": not stale,
        "status": "ok" if not stale else "attention",
        "message": message,
        "latest_decision_age_seconds": age_seconds,
        "latest_minute_age_seconds": minute_age_seconds,
        "stale_limit_seconds": stale_limit,
        "market_window": window.__dict__,
    }


def _etf_data_freshness(db_path: Path) -> dict[str, Any]:
    daily = _etf_query(db_path, "SELECT MAX(trade_date) AS latest_daily_date FROM etf_daily")
    minute = _etf_query(
        db_path,
        """
        SELECT trade_time AS latest_minute_time, source AS latest_minute_source
        FROM etf_minute
        ORDER BY trade_time DESC
        LIMIT 1
        """,
    )
    daily_row = daily[0] if daily else {}
    minute_row = minute[0] if minute else {}
    return {
        "latest_daily_date": daily_row.get("latest_daily_date"),
        "latest_minute_time": minute_row.get("latest_minute_time"),
        "latest_minute_source": minute_row.get("latest_minute_source"),
    }


def _load_strategy_document() -> dict[str, Any]:
    if PARAMS_FILE.exists():
        try:
            parsed = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                parsed.setdefault("params", default_strategy_params())
                parsed.setdefault("bounds", _param_bounds())
                return parsed
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "version": 1,
        "updated_at": None,
        "source": "env_defaults",
        "reason": "strategy_params.json 尚未生成，当前显示环境默认参数",
        "last_self_learning_at": None,
        "params": default_strategy_params(),
        "bounds": _param_bounds(),
    }


def _param_bounds() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "minimum": spec.minimum,
            "maximum": spec.maximum,
            "max_step": spec.max_step,
            "kind": spec.kind,
        }
        for name, spec in PARAM_SPECS.items()
    }


def _decode_json_columns(row: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(row)
    for key in ("metrics_json", "proposal_json", "applied_json", "changes_json"):
        if key in decoded:
            decoded[key.removesuffix("_json")] = _loads(decoded.pop(key))
    return decoded


def _safe_storage_path(value: Any) -> Path | None:
    raw = Path(str(value))
    path = raw if raw.is_absolute() else settings.project_root / raw
    try:
        resolved = path.resolve()
    except OSError:
        return None
    allowed_roots = [settings.storage_dir.resolve(), (settings.project_root / "storage").resolve()]
    if any(str(resolved).startswith(str(root)) for root in allowed_roots):
        return resolved
    return None


def _age_seconds(value: Any) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BEIJING_TZ)
        return round((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds(), 2)
    except ValueError:
        return None


def _loads(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _now() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
