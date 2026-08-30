from __future__ import annotations

from typing import Any

from app.config import settings
from data.tushare_client import Quote
from strategies.allocation_engine import AllocationEngine
from strategies.config import strategy_spec
from strategies.hot_leader import build_hot_leader
from strategies.quality_growth import build_quality_growth
from strategies.regime_engine import RegimeEngine
from strategies.robust_hybrid import build_robust_hybrid


class MultiStrategyEngine:
    def build(
        self,
        state: dict[str, Any],
        sentiment: dict[str, Any],
        leader: dict[str, Any],
        quotes: list[Quote],
        robust_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        if not settings.multi_strategy_enabled or settings.multi_strategy_mode == "disabled":
            return _disabled_plan()

        regime = RegimeEngine().evaluate(state, sentiment, quotes)
        allocation = AllocationEngine().allocate(regime)
        budgets = allocation["strategy_budgets"]
        strategies = [
            build_robust_hybrid(robust_bundle, budgets["robust_hybrid"]),
            build_quality_growth(quotes, state, sentiment, budgets["quality_growth"]),
            build_hot_leader(quotes, leader, sentiment, regime, budgets["hot_leader"]),
        ]
        portfolio = _combine(strategies, regime, allocation, sentiment)
        return {
            "version": "v1.9",
            "mode": settings.multi_strategy_mode,
            "regime": regime,
            "allocation": allocation,
            "strategies": strategies,
            "portfolio_signal": portfolio,
            "generated_for": "paper_and_dashboard" if settings.multi_strategy_mode == "shadow" else "portfolio_signal",
        }


def _combine(
    strategy_results: list[dict[str, Any]],
    regime: dict[str, Any],
    allocation: dict[str, Any],
    sentiment: dict[str, Any],
) -> dict[str, Any]:
    merged: dict[str, dict[str, Any]] = {}
    for result in strategy_results:
        for candidate in result.get("candidates") or []:
            symbol = str(candidate.get("symbol") or "")
            if not symbol:
                continue
            if symbol not in merged:
                copied = dict(candidate)
                copied["strategy_ids"] = [str(candidate.get("strategy_id"))]
                copied["strategy_weights"] = {str(candidate.get("strategy_id")): _float(candidate.get("target_weight"))}
                merged[symbol] = copied
                continue
            existing = merged[symbol]
            strategy_id = str(candidate.get("strategy_id"))
            existing["strategy_ids"] = list(dict.fromkeys(existing["strategy_ids"] + [strategy_id]))
            existing["strategy_weights"][strategy_id] = _float(candidate.get("target_weight"))
            existing["target_weight"] = min(0.10, _float(existing.get("target_weight")) + _float(candidate.get("target_weight")))
            existing["position"] = existing["target_weight"]
            if _float(candidate.get("strategy_score")) > _float(existing.get("strategy_score")):
                for key in ("strategy_score", "reasoning", "selection_mode", "entry_note"):
                    existing[key] = candidate.get(key, existing.get(key))

    recommendations = sorted(merged.values(), key=lambda item: _float(item.get("strategy_score")), reverse=True)[:15]
    for item in recommendations:
        labels = [strategy_spec(strategy_id)["label"] for strategy_id in item.get("strategy_ids") or []]
        item["strategy_label"] = " + ".join(labels)
        item["position"] = round(_float(item.get("target_weight")), 4)
        item["reasoning"] = f"[{item['strategy_label']}] {item.get('reasoning') or ''}".strip()

    regime_name = str(regime.get("regime") or "range")
    risk_off = regime_name == "risk_off" or str(sentiment.get("trade_permission") or "").upper() == "NO_BUY"
    target_exposure = sum(_float(item.get("target_weight")) for item in recommendations)
    signal = "SELL" if risk_off else ("BUY" if recommendations else "HOLD")
    return {
        "output_type": "v1_9_multi_strategy_portfolio",
        "is_multi_strategy": True,
        "signal": signal,
        "position": round(0.0 if signal == "SELL" else target_exposure, 4),
        "portfolio_target_exposure": round(target_exposure, 4),
        "risk_level": "high" if risk_off else ("medium" if regime_name in {"range", "overheat"} else "normal"),
        "recommendations": [] if signal == "SELL" else recommendations,
        "watchlist": [],
        "recommendation_count": 0 if signal == "SELL" else len(recommendations),
        "candidate_count": sum(int(result.get("candidate_count") or 0) for result in strategy_results),
        "market_stage": regime_name,
        "market_stage_reason": str(regime.get("reason") or ""),
        "market_sentiment": sentiment,
        "allocation": allocation,
        "strategy_status": [
            {
                "strategy_id": result["strategy_id"],
                "strategy_label": result["strategy_label"],
                "budget": result["budget"],
                "target_exposure": result["target_exposure"],
                "recommendation_count": result["recommendation_count"],
                "status": result["status"],
                "reason": result["reason"],
            }
            for result in strategy_results
        ],
        "no_recommendation_reason": "市场进入防守状态" if risk_off else (None if recommendations else "各策略均未通过可交易性与风控门槛"),
        "selection_logic": "市场风格决定策略预算；策略独立选股；组合层去重、限单股和保留现金",
        "risk_flags": ["multi_strategy_shadow" if settings.multi_strategy_mode == "shadow" else "multi_strategy_active"],
    }


def _disabled_plan() -> dict[str, Any]:
    return {
        "version": "v1.9",
        "mode": "disabled",
        "regime": {"regime": "range", "reason": "多策略功能未启用"},
        "allocation": {},
        "strategies": [],
        "portfolio_signal": {
            "signal": "HOLD",
            "position": 0.0,
            "recommendations": [],
            "recommendation_count": 0,
        },
        "generated_for": "disabled",
    }


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
