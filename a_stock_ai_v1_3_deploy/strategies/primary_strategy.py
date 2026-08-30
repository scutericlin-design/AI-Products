"""Selects the auditable primary strategy without disabling observing sleeves."""

from __future__ import annotations

from typing import Any

from app.config import settings


HYBRID_ALPHA_ID = "hybrid_alpha"
HYBRID_ALPHA_LABEL = "Hybrid Alpha"


def apply_primary_strategy(final_signal: dict[str, Any]) -> dict[str, Any]:
    """Annotate the risk-checked signal used by push and paper execution."""
    if settings.primary_strategy_id == HYBRID_ALPHA_ID:
        return _hybrid_alpha_signal(final_signal)
    return _multi_strategy_signal(final_signal)


def primary_strategy_summary(signal: dict[str, Any]) -> dict[str, Any]:
    strategy_id = str(signal.get("primary_strategy_id") or settings.primary_strategy_id)
    return {
        "strategy_id": strategy_id,
        "strategy_label": str(signal.get("strategy_label") or _label(strategy_id)),
        "strategy_version": str(signal.get("strategy_version") or "v1.9"),
        "parameter_lock_enabled": bool(
            strategy_id == HYBRID_ALPHA_ID and settings.hybrid_alpha_lock_parameters
        ),
        "signal": str(signal.get("signal") or "HOLD"),
        "recommendation_count": int(signal.get("recommendation_count") or 0),
        "selection_logic": str(signal.get("selection_logic") or ""),
    }


def _hybrid_alpha_signal(signal: dict[str, Any]) -> dict[str, Any]:
    updated = dict(signal)
    updated["recommendations"] = [_tag_hybrid_alpha(item) for item in list(signal.get("recommendations") or [])]
    updated["watchlist"] = [_tag_hybrid_alpha(item) for item in list(signal.get("watchlist") or [])]
    updated["strategy_id"] = HYBRID_ALPHA_ID
    updated["strategy_label"] = HYBRID_ALPHA_LABEL
    updated["strategy_version"] = "v1.7_hybrid_alpha_restored"
    updated["primary_strategy_id"] = HYBRID_ALPHA_ID
    updated["selection_logic"] = (
        "Hybrid Alpha 主策略：市场状态、情绪、龙头强度、质量和可交易性共同确认；"
        "AI 只能降低风险或确认候选，不能新增标的。"
    )
    flags = list(updated.get("risk_flags") or [])
    flags.append("hybrid_alpha_primary")
    if settings.hybrid_alpha_lock_parameters:
        flags.append("hybrid_alpha_parameter_lock")
    updated["risk_flags"] = list(dict.fromkeys(flags))
    return updated


def _multi_strategy_signal(signal: dict[str, Any]) -> dict[str, Any]:
    updated = dict(signal)
    updated["strategy_id"] = "multi_strategy"
    updated["strategy_label"] = "多策略组合"
    updated["strategy_version"] = "v1.9_multi_strategy"
    updated["primary_strategy_id"] = "multi_strategy"
    return updated


def _tag_hybrid_alpha(candidate: Any) -> dict[str, Any]:
    item = dict(candidate) if isinstance(candidate, dict) else {}
    item["strategy_id"] = HYBRID_ALPHA_ID
    item["strategy_ids"] = [HYBRID_ALPHA_ID]
    item["strategy_label"] = HYBRID_ALPHA_LABEL
    item["strategy_weights"] = {HYBRID_ALPHA_ID: _float(item.get("position"))}
    return item


def _label(strategy_id: str) -> str:
    return HYBRID_ALPHA_LABEL if strategy_id == HYBRID_ALPHA_ID else "多策略组合"


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
