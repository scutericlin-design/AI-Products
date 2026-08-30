from __future__ import annotations

from typing import Any

from strategies.base import strategy_result


def build_robust_hybrid(bundle: dict[str, Any], budget: float) -> dict[str, Any]:
    candidates = []
    for item in list(bundle.get("recommendations") or []):
        candidate = dict(item)
        candidate["strategy_score"] = candidate.get("rank_score", 0.0)
        candidate["strategy_reason"] = "沿用已验证的质量、趋势、情绪与可交易性综合排序"
        candidates.append(candidate)
    reason = str(bundle.get("no_recommendation_reason") or "沿用当前稳健策略的盘中候选池")
    return strategy_result("robust_hybrid", budget, candidates, reason)
