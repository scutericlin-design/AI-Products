from __future__ import annotations
from typing import Any
from hot_leader_strategy.config import load_settings
from hot_leader_strategy.paper import ACCOUNT_ID
from hot_leader_strategy.storage import HotLeaderStore

def build_hot_leader_dashboard_payload() -> dict[str, Any]:
    settings = load_settings(ensure_dirs=False)
    if not settings.db_path.exists():
        return _empty_payload(settings, "热点龙头模拟账户尚未初始化")
    store = HotLeaderStore(settings.db_path, read_only=True)
    latest = store.latest_signal()
    plan = latest.get("payload") or {}
    return {
        "generated_at": latest.get("created_at"),
        "read_only": True,
        "no_real_orders": True,
        "strategy": {"strategy_id": "hot_theme_leader", "strategy_version": settings.strategy_version, "execution_mode": "independent_local_paper_only", "data_policy": "TuShare中转优先，AKShare仅日线备用；热点按可审计行业代理"},
        "latest_decision": {"as_of": plan.get("as_of"), "signal": plan.get("signal", "HOLD"), "target_exposure": plan.get("target_exposure", 0), "market_state": plan.get("market_state", {}), "hot_themes": plan.get("hot_themes", []), "recommendations": plan.get("recommendations", []), "risk_flags": plan.get("risk_flags", [])},
        "intraday": {"enabled": settings.intraday_enabled, "interval_minutes": settings.intraday_interval_minutes, "entry_enabled": settings.intraday_entry_enabled, "latest_quotes": store.latest_intraday_snapshots(), "recent_events": store.recent_intraday_events()},
        "account": store.paper_snapshot(ACCOUNT_ID, settings.paper_initial_cash),
        "recent_orders": store.recent_orders(),
        "backtests": store.recent_backtests(),
        "data_quality": store.recent_quality(),
    }


def _empty_payload(settings: Any, detail: str) -> dict[str, Any]:
    account = {
        "account": {"account_id": ACCOUNT_ID, "cash": 0.0, "initial_cash": settings.paper_initial_cash},
        "positions": [],
        "cash": 0.0,
        "market_value": 0.0,
        "equity": 0.0,
        "return_pct": 0.0,
    }
    return {
        "generated_at": None,
        "read_only": True,
        "no_real_orders": True,
        "strategy": {"strategy_id": "hot_theme_leader", "strategy_version": settings.strategy_version, "execution_mode": "independent_local_paper_only"},
        "latest_decision": {"signal": "HOLD", "target_exposure": 0.0, "market_state": {"reason": detail}, "hot_themes": [], "recommendations": [], "risk_flags": []},
        "intraday": {"enabled": settings.intraday_enabled, "interval_minutes": settings.intraday_interval_minutes, "entry_enabled": settings.intraday_entry_enabled, "latest_quotes": [], "recent_events": []},
        "account": account,
        "recent_orders": [],
        "backtests": [],
        "data_quality": [{"check_type": "storage", "status": "waiting", "detail": detail, "as_of": None}],
    }
