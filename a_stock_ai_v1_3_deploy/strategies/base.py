from __future__ import annotations

from typing import Any

from strategies.config import strategy_spec


def allocate_candidates(
    strategy_id: str,
    candidates: list[dict[str, Any]],
    budget: float,
) -> list[dict[str, Any]]:
    spec = strategy_spec(strategy_id)
    eligible = [item for item in candidates if str(item.get("action") or "").upper() == "BUY"]
    eligible = eligible[: int(spec["max_names"])]
    if not eligible or budget <= 0:
        return []

    scores = [max(_float(item.get("strategy_score") or item.get("rank_score")), 1.0) for item in eligible]
    total_score = sum(scores)
    max_single_weight = float(spec["max_single_weight"])
    allocation = min(float(budget), max_single_weight * len(eligible))
    allocated: list[dict[str, Any]] = []
    remaining = allocation
    for index, item in enumerate(eligible):
        weight = min(max_single_weight, allocation * scores[index] / total_score)
        if index == len(eligible) - 1:
            weight = min(max_single_weight, remaining)
        remaining = max(0.0, remaining - weight)
        updated = dict(item)
        updated["strategy_id"] = strategy_id
        updated["strategy_label"] = spec["label"]
        updated["target_weight"] = round(weight, 4)
        updated["position"] = round(weight, 4)
        updated["horizon"] = spec["horizon"]
        updated["strategy_risk_level"] = spec["risk_level"]
        allocated.append(updated)
    return allocated


def strategy_result(
    strategy_id: str,
    budget: float,
    candidates: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    allocated = allocate_candidates(strategy_id, candidates, budget)
    return {
        "strategy_id": strategy_id,
        "strategy_label": strategy_spec(strategy_id)["label"],
        "budget": round(max(budget, 0.0), 4),
        "target_exposure": round(sum(_float(item.get("target_weight")) for item in allocated), 4),
        "candidate_count": len(candidates),
        "recommendation_count": len(allocated),
        "signal": "BUY" if allocated else "HOLD",
        "status": "active" if allocated else "observe",
        "reason": reason,
        "candidates": allocated,
    }


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
