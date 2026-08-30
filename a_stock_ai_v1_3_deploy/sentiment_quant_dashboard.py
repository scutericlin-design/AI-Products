from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dashboard.data import build_dashboard_payload


INTRADAY_REFRESH_SECONDS = 180


def build_sentiment_quant_dashboard_payload() -> dict[str, Any]:
    """Read-only bridge from the TuShare/DeepSeek research workspace to Dashboard.

    It never imports API keys, calls providers, generates a signal, or submits an
    order during a dashboard request.  The page is therefore safe to refresh.
    """
    root = Path(os.getenv("A_SHARE_QUANT_ROOT") or Path(__file__).resolve().parents[1] / "a_share_quant_tool")
    data_dir = Path(os.getenv("A_SHARE_QUANT_DATA_DIR") or root / "data")
    processed = data_dir / "processed"
    sentiment = _read_json(processed / "sentiment_latest.json")
    research = _read_json(processed / "daily_research_latest.json")
    backtest = _read_json(processed / "backtest_latest.json")
    signals = _read_rows(processed / "signals_latest.json")
    manifests = _latest_manifests(data_dir / "raw")
    freshness = _freshness(sentiment, manifests)
    intraday = _intraday_observation()
    snapshot = sentiment.get("metrics") if isinstance(sentiment.get("metrics"), dict) else {}
    state = str(sentiment.get("state") or "BLOCKED").upper()
    exposure = _number(sentiment.get("target_exposure"))
    data_blocked = not sentiment or freshness["status"] != "OK"
    as_of = str(snapshot.get("date") or sentiment.get("date") or "")
    current_candidates = [] if data_blocked else [
        row for row in signals if str(row.get("date") or row.get("as_of") or "") == as_of
    ][:20]
    current_research = research if not data_blocked and str(research.get("trade_date") or "") == as_of else {}
    reasons = list(sentiment.get("reasons") or [])
    if data_blocked:
        reasons.insert(0, freshness["reason"])
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "read_only": True,
        "no_real_orders": True,
        "strategy": {
            "strategy_id": "sentiment_quant_tushare",
            "execution_mode": "research_and_paper_only",
            "data_source": "TuShare archive",
            "ai_policy": "DeepSeek only explains timestamped facts; it cannot change signals or place orders.",
        },
        "latest_decision": {
            "as_of": as_of or None,
            "state": "BLOCKED" if data_blocked else state,
            "target_exposure": 0.0 if data_blocked else exposure,
            "score": sentiment.get("score"),
            "reasons": reasons or ["尚未生成盘后情绪快照"],
            "metrics": snapshot,
        },
        "data_quality": freshness,
        "intraday_observation": intraday,
        "candidates": current_candidates,
        "research_note": current_research,
        "backtest": backtest,
        "setup": {
            "root": str(root),
            "required_reports": ["sentiment_latest.json", "signals_latest.json", "daily_research_latest.json"],
            "next_action": "在 a_share_quant_tool 完成 TuShare 盘后归档与情绪计算后刷新本页。",
        },
    }


def _intraday_observation() -> dict[str, Any]:
    """Expose the existing engine snapshot as observation-only intraday context.

    The Dashboard never pulls a provider on page load.  It only reads the last
    completed perception cycle already written by the realtime engine, so page
    refreshes cannot create extra market-data calls or orders.
    """
    try:
        payload = build_dashboard_payload()
    except Exception as exc:
        return {
            "mode": "observation_only",
            "refresh_interval_seconds": INTRADAY_REFRESH_SECONDS,
            "status": "BLOCKED",
            "reason": f"无法读取实时引擎快照：{type(exc).__name__}",
            "actionable": False,
        }

    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    latest = payload.get("latest_cycle") if isinstance(payload.get("latest_cycle"), dict) else {}
    sentiment = payload.get("sentiment") if isinstance(payload.get("sentiment"), dict) else {}
    window = health.get("market_window") if isinstance(health.get("market_window"), dict) else {}
    age_seconds = _number(health.get("latest_cycle_age_seconds"))
    observed_at = latest.get("finished_at") or latest.get("started_at")
    market_open = bool(window.get("is_open"))
    cycle_ok = latest.get("status") == "ok" and bool(observed_at) and bool(sentiment)
    stale_limit = max(INTRADAY_REFRESH_SECONDS + 60, _number(health.get("stale_limit_seconds")))
    fresh = cycle_ok and (not market_open or age_seconds <= stale_limit)
    if not market_open:
        status, reason = "CLOSED", f"当前非交易时段：{window.get('phase') or 'closed'}；展示最近盘中快照"
    elif not fresh:
        status, reason = "BLOCKED", health.get("message") or "实时快照过期或不完整"
    else:
        status, reason = "LIVE", f"实时引擎每 {INTRADAY_REFRESH_SECONDS // 60} 分钟刷新；本页只读展示"

    metrics = {
        "limit_up_count": sentiment.get("limit_up_count"),
        "near_limit_up_count": sentiment.get("near_limit_up_count"),
        "limit_down_count": sentiment.get("limit_down_count"),
        "near_limit_down_count": sentiment.get("near_limit_down_count"),
        "breadth": sentiment.get("breadth"),
        "avg_pct_change": sentiment.get("avg_pct_change"),
        "total_amount_yi": sentiment.get("total_amount_yi"),
        "coverage_count": sentiment.get("coverage_count"),
        "panic_score": sentiment.get("panic_score"),
    }
    return {
        "mode": "observation_only",
        "refresh_interval_seconds": INTRADAY_REFRESH_SECONDS,
        "status": status,
        "reason": str(reason),
        "actionable": False,
        "observed_at": observed_at,
        "cycle_id": latest.get("cycle_id"),
        "age_seconds": age_seconds,
        "market_open": market_open,
        "observation_state": sentiment.get("risk_appetite") or "unknown",
        "trade_permission": sentiment.get("trade_permission") or "NO_BUY",
        "sentiment_score": sentiment.get("sentiment_score"),
        "coverage_level": sentiment.get("coverage_level"),
        "metrics": metrics,
        "reasons": list(sentiment.get("hard_rules") or sentiment.get("flags") or []),
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_rows(path: Path) -> list[dict[str, Any]]:
    value = _read_json(path)
    rows = value.get("signals") if isinstance(value.get("signals"), list) else []
    return [row for row in rows if isinstance(row, dict)]


def _latest_manifests(raw: Path) -> dict[str, Any]:
    records: list[tuple[str, dict[str, Any]]] = []
    if raw.is_dir():
        for path in raw.glob("*/trade_date=*/manifest.json"):
            payload = _read_json(path)
            if payload:
                records.append((str(payload.get("trade_date") or ""), payload))
    latest_date = max((date for date, _ in records), default="")
    latest = [payload for date, payload in records if date == latest_date]
    required = {"daily", "daily_basic", "stk_limit", "suspend_d", "stock_basic", "limit_up_pool", "broken_board_pool", "limit_down_pool", "limit_cpt_list", "moneyflow"}
    present = {str(row.get("dataset") or "") for row in latest}
    missing = sorted(required - present)
    return {
        "as_of": latest_date or None,
        "status": "OK" if latest_date and not missing else "BLOCKED",
        "datasets_present": sorted(present),
        "missing_datasets": missing,
        "snapshot_count": len(latest),
        "reason": "TuShare 盘后数据归档完整" if latest_date and not missing else "缺少盘后归档，禁止将页面结果用于交易",
    }


def _freshness(sentiment: dict[str, Any], manifests: dict[str, Any]) -> dict[str, Any]:
    status = manifests["status"]
    as_of = manifests["as_of"]
    metrics = sentiment.get("metrics") if isinstance(sentiment.get("metrics"), dict) else {}
    if sentiment and as_of and str(metrics.get("date") or "") != as_of:
        status = "BLOCKED"
        reason = "情绪快照日期与归档交易日不一致"
    else:
        reason = manifests["reason"]
    return {**manifests, "status": status, "reason": reason}


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
