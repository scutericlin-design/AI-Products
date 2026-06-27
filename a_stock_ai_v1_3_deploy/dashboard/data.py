from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.config import settings
from learning.strategy_params import PARAMS_FILE, PARAM_SPECS, default_strategy_params
from scheduler.trading_calendar import BEIJING_TZ, current_trading_window
from simulation.paper_trading import current_paper_account


def build_dashboard_payload() -> dict[str, Any]:
    latest_cycle = _latest_cycle()
    latest_payload = _loads(latest_cycle.get("payload_json")) if latest_cycle else {}
    final_signal = _dict(latest_payload.get("final_signal"))
    state = _dict(latest_payload.get("state"))
    sentiment = _dict(latest_payload.get("market_sentiment") or final_signal.get("market_sentiment"))
    leader = _dict(latest_payload.get("leader"))
    push = _dict(latest_payload.get("push"))
    recommendations = _list(final_signal.get("recommendations"))
    watchlist = _list(final_signal.get("watchlist"))

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
        "paper_account": _paper_account_payload(),
        "recent_orders": _recent_orders(),
        "recent_trades": _recent_trades(),
        "paper_equity_curve": _paper_equity_curve(),
        "recent_cycles": _recent_cycles(),
        "strategy": _strategy_payload(),
        "backtests": _backtest_payload(),
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
        "dashboard_version": "v1.8",
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
    }


def _signal_payload(final_signal: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal": final_signal.get("signal", "HOLD"),
        "position": _float(final_signal.get("position"), 0.0),
        "risk_level": final_signal.get("risk_level", "normal"),
        "reasoning": final_signal.get("reasoning", ""),
        "ai_provider": final_signal.get("ai_provider"),
        "ai_error": final_signal.get("ai_error"),
        "selection_logic": final_signal.get("selection_logic"),
        "market_stage": final_signal.get("market_stage"),
        "market_stage_reason": final_signal.get("market_stage_reason"),
        "no_recommendation_reason": final_signal.get("no_recommendation_reason"),
        "risk_flags": _list(final_signal.get("risk_flags")),
    }


def _paper_account_payload() -> dict[str, Any]:
    try:
        return current_paper_account()
    except Exception as exc:
        return {"error": str(exc), "positions": {}, "equity": 0.0, "cash": 0.0, "market_value": 0.0}


def _recent_orders(limit: int = 40) -> list[dict[str, Any]]:
    rows = _query(
        """
        SELECT cycle_id, symbol, name, side, status, quantity, price, reason, created_at
        FROM paper_orders
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return rows


def _recent_trades(limit: int = 30) -> list[dict[str, Any]]:
    rows = _query(
        """
        SELECT cycle_id, symbol, name, side, quantity, price, amount, fee, tax, slippage, realized_pnl, created_at
        FROM paper_trades
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return rows


def _paper_equity_curve(limit: int = 120) -> list[dict[str, Any]]:
    rows = _query(
        """
        SELECT created_at, cash, equity, market_value, realized_pnl, unrealized_pnl, status
        FROM paper_account_snapshots
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return list(reversed(rows))


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
            "先判定市场阶段：confirmed / transition / overheat / cold / risk_off",
            "再识别选股模式：breakout / pullback / watch，涨停和接近涨停不进入买入推荐",
            "基础质地过滤 ST、退市风险、低流动性、异常振幅和价格异常",
            "MiniMax 只对系统候选池做二次确认，不能凭空新增股票",
            "模拟盘执行 T+1、100 股一手、滑点、佣金、印花税和目标仓位约束",
        ],
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
    if not settings.db_path.exists():
        return []
    uri = f"file:{quote(str(settings.db_path), safe='/')}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


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
