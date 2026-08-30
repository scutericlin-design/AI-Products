from __future__ import annotations

from typing import Any

from data.tushare_client import Quote
from engine.tradability_engine import evaluate_tradability
from strategies.base import strategy_result


def build_hot_leader(
    quotes: list[Quote],
    leader: dict[str, Any],
    sentiment: dict[str, Any],
    regime: dict[str, Any],
    budget: float,
) -> dict[str, Any]:
    regime_name = str(regime.get("regime") or "range")
    permission = str(sentiment.get("trade_permission") or "NO_BUY").upper()
    sentiment_score = _float(sentiment.get("sentiment_score"))
    if regime_name not in {"trend", "structural", "overheat"}:
        return strategy_result("hot_leader", budget, [], "热点策略仅在结构性或趋势行情中启用")
    if permission != "BUY_ALLOWED" or sentiment_score < 60:
        return strategy_result("hot_leader", budget, [], "市场情绪不足，热点仓保持观察")

    strength_map = {str(item.get("symbol")): _float(item.get("strength")) for item in leader.get("candidates") or []}
    candidates: list[dict[str, Any]] = []
    for quote in quotes:
        strength = strength_map.get(quote.symbol, 0.0)
        if strength < 75 or quote.pct_change < 1.5 or quote.pct_change > 7.5:
            continue
        tradability = evaluate_tradability(quote)
        if not tradability["can_buy"] or tradability["is_limit_up"] or tradability["near_limit_up"]:
            continue
        score = min(100.0, strength * 0.58 + tradability["score"] * 0.24 + sentiment_score * 0.18)
        if score < 76:
            continue
        candidates.append(
            {
                "symbol": quote.symbol,
                "name": quote.name,
                "action": "BUY",
                "selection_mode": "hot_leader",
                "strategy_score": round(score, 2),
                "current_price": round(quote.price, 2),
                "pct_change": round(quote.pct_change, 2),
                "amount_yi": round(quote.amount_yi, 2),
                "volume_ratio": round(quote.volume_ratio, 2),
                "leader_strength": round(strength, 2),
                "buy_range": {
                    "low": tradability["buy_range_low"],
                    "high": tradability["buy_range_high"],
                },
                "max_buy_price": tradability["max_buy_price"],
                "stop_loss": round(quote.price * 0.955, 2),
                "target_take_profit_pct": 0.12,
                "trailing_stop_pct": 0.06,
                "position_plan": "试错小仓，确认强度后再考虑加仓",
                "buy_mode": "momentum_entry",
                "entry_note": "仅在未涨停、未接近涨停且价格位于买入区间时执行",
                "can_buy": True,
                "is_limit_up": False,
                "near_limit_up": False,
                "tradability": tradability,
                "reasoning": f"龙头强度 {strength:.0f}，情绪 {sentiment_score:.0f}，成交额 {quote.amount_yi:.1f} 亿",
                "risk_flags": ["热点策略高波动", "禁止追涨停板"],
            }
        )
    candidates.sort(key=lambda item: item["strategy_score"], reverse=True)
    return strategy_result("hot_leader", budget, candidates, "只选择仍可成交的强势龙头，不追涨停或接近涨停标的")


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
