from __future__ import annotations

from typing import Any

from qgarp_strategy.config import load_settings
from qgarp_strategy.storage import QGARPStore


def build_qgarp_dashboard_payload() -> dict[str, Any]:
    settings = load_settings(ensure_dirs=False)
    if not settings.db_path.exists():
        return _empty_payload(settings, "Q-GARP 模拟账户尚未初始化")
    store = QGARPStore(settings.db_path, read_only=True)
    latest = store.latest_signal()
    plan = latest.get("payload") or {}
    account = store.paper_snapshot("qgarp_alpha_paper", settings.paper_initial_cash)
    return {
        "generated_at": latest.get("created_at"), "read_only": True, "no_real_orders": True,
        "strategy": {"strategy_id": "qgarp_alpha", "strategy_version": plan.get("strategy_version", settings.strategy_version), "execution_mode": "local_paper_only", "data_policy": "TuShare中转优先；AKShare仅备用价格；财务缺失不买入"},
        "latest_decision": {"signal_id": latest.get("signal_id"), "as_of": plan.get("as_of"), "signal": plan.get("signal", "HOLD"), "target_exposure": plan.get("target_exposure", 0.0), "market_regime": plan.get("market_regime", "--"), "market_regime_reason": plan.get("market_regime_reason", "等待数据"), "recommendations": plan.get("recommendations", []), "risk_flags": plan.get("risk_flags", []), "selection_logic": plan.get("selection_logic", "")},
        "account": account, "recent_orders": store.recent_orders(), "equity_curve": store.equity_curve(),
        "recent_signals": store.recent_signals(), "backtests": store.recent_backtests(), "data_quality": store.recent_quality_checks(),
    }


def _empty_payload(settings: Any, detail: str) -> dict[str, Any]:
    account = {
        "account": {"account_id": "qgarp_alpha_paper", "cash": 0.0, "initial_cash": settings.paper_initial_cash},
        "positions": [],
        "cash": 0.0,
        "equity": 0.0,
        "market_value": 0.0,
        "return_pct": 0.0,
    }
    return {
        "generated_at": None,
        "read_only": True,
        "no_real_orders": True,
        "strategy": {"strategy_id": "qgarp_alpha", "strategy_version": settings.strategy_version, "execution_mode": "local_paper_only"},
        "latest_decision": {"signal": "HOLD", "target_exposure": 0.0, "market_regime": "--", "market_regime_reason": detail, "recommendations": [], "risk_flags": []},
        "account": account,
        "recent_orders": [],
        "equity_curve": [],
        "recent_signals": [],
        "backtests": [],
        "data_quality": [{"check_type": "storage", "status": "waiting", "detail": detail, "as_of": None}],
    }
