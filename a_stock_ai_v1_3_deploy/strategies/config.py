from __future__ import annotations

from typing import Any


STRATEGY_SPECS: dict[str, dict[str, Any]] = {
    "robust_hybrid": {
        "label": "稳健核心",
        "max_names": 3,
        "max_single_weight": 0.08,
        "horizon": "5-30 个交易日",
        "risk_level": "medium",
    },
    "quality_growth": {
        "label": "优质成长",
        "max_names": 5,
        "max_single_weight": 0.06,
        "horizon": "1-6 个月",
        "risk_level": "medium",
    },
    "hot_leader": {
        "label": "热点龙头",
        "max_names": 2,
        "max_single_weight": 0.04,
        "horizon": "1-10 个交易日",
        "risk_level": "high",
    },
}


ALLOCATION_PROFILES: dict[str, dict[str, float]] = {
    "risk_off": {"robust_hybrid": 0.15, "quality_growth": 0.10, "hot_leader": 0.00, "cash": 0.75},
    "range": {"robust_hybrid": 0.35, "quality_growth": 0.30, "hot_leader": 0.05, "cash": 0.30},
    "structural": {"robust_hybrid": 0.40, "quality_growth": 0.35, "hot_leader": 0.10, "cash": 0.15},
    "trend": {"robust_hybrid": 0.30, "quality_growth": 0.30, "hot_leader": 0.25, "cash": 0.15},
    "overheat": {"robust_hybrid": 0.20, "quality_growth": 0.20, "hot_leader": 0.15, "cash": 0.45},
}


def strategy_spec(strategy_id: str) -> dict[str, Any]:
    return dict(STRATEGY_SPECS[strategy_id])
