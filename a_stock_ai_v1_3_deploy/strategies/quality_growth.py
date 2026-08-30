from __future__ import annotations

from typing import Any

from app.config import settings
from data.fundamental_client import FundamentalClient
from data.tushare_client import Quote
from engine.quality_engine import evaluate_quality
from engine.tradability_engine import evaluate_tradability
from strategies.base import strategy_result


def build_quality_growth(
    quotes: list[Quote],
    state: dict[str, Any],
    sentiment: dict[str, Any],
    budget: float,
) -> dict[str, Any]:
    if str(state.get("state") or "").upper() in {"NO_DATA", "DOWNTREND"}:
        return strategy_result("quality_growth", budget, [], "市场处于防守状态，优质成长策略只观察")
    if str(sentiment.get("trade_permission") or "BUY_ALLOWED").upper() == "NO_BUY":
        return strategy_result("quality_growth", budget, [], "情绪风控禁止新增仓位")

    client = FundamentalClient()
    candidates: list[dict[str, Any]] = []
    unavailable_count = 0
    for quote in quotes[:20]:
        tradability = evaluate_tradability(quote)
        realtime_quality = evaluate_quality(quote)
        profile = client.get_profile(quote.symbol)
        if not profile.get("available"):
            unavailable_count += 1
            continue
        score, factors = _quality_growth_score(quote, profile, realtime_quality)
        if score < 72 or not tradability.get("can_buy"):
            continue
        candidates.append(
            {
                "symbol": quote.symbol,
                "name": quote.name,
                "action": "BUY",
                "selection_mode": "quality_growth",
                "strategy_score": score,
                "current_price": round(quote.price, 2),
                "pct_change": round(quote.pct_change, 2),
                "amount_yi": round(quote.amount_yi, 2),
                "volume_ratio": round(quote.volume_ratio, 2),
                "buy_range": {
                    "low": round(quote.price * 0.985, 2),
                    "high": min(tradability["buy_range_high"], round(quote.price * 1.005, 2)),
                },
                "max_buy_price": min(tradability["max_buy_price"], round(quote.price * 1.005, 2)),
                "stop_loss": round(quote.price * 0.92, 2),
                "target_take_profit_pct": 0.20,
                "trailing_stop_pct": 0.12,
                "position_plan": "分两次建仓，基本面与趋势失效时减仓",
                "buy_mode": "quality_pullback",
                "entry_note": "避免追高，等待买入区间内成交",
                "can_buy": tradability["can_buy"],
                "is_limit_up": tradability["is_limit_up"],
                "near_limit_up": tradability["near_limit_up"],
                "quality": realtime_quality,
                "fundamental_profile": profile,
                "quality_growth_factors": factors,
                "reasoning": _reasoning(profile, factors),
                "risk_flags": list(realtime_quality.get("warnings") or []),
            }
        )
    candidates.sort(key=lambda item: item["strategy_score"], reverse=True)
    if unavailable_count:
        reason = f"{unavailable_count} 只候选缺少新鲜财务数据，未作为买入依据"
    else:
        reason = "基于 TuShare 财务指标、行业匹配与盘中可交易性筛选"
    return strategy_result("quality_growth", budget, candidates, reason)


def _quality_growth_score(
    quote: Quote,
    profile: dict[str, Any],
    realtime_quality: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    values = profile.get("fundamentals") or {}
    profitability = _score_ge(values.get("roe"), ((8, 45), (12, 70), (18, 95)))
    growth = (
        _score_ge(values.get("revenue_yoy"), ((0, 35), (10, 65), (25, 95)))
        + _score_ge(values.get("profit_yoy"), ((0, 30), (12, 70), (30, 95)))
    ) / 2
    cashflow = _score_ge(values.get("operating_cashflow_per_share"), ((0, 25), (0.5, 65), (1.5, 90)))
    valuation = _valuation_score(values.get("pe_ttm"), values.get("pb"))
    industry = _industry_score(str(profile.get("industry") or ""))
    trend = min(100.0, 55 + max(min(quote.pct_change, 6.0), -4.0) * 5 + min(quote.volume_ratio, 3.0) * 5)
    factors = {
        "profitability": round(profitability, 2),
        "growth": round(growth, 2),
        "cashflow": round(cashflow, 2),
        "valuation": round(valuation, 2),
        "industry": round(industry, 2),
        "trend": round(trend, 2),
        "realtime_quality": round(float(realtime_quality.get("score") or 0.0), 2),
    }
    score = (
        profitability * 0.20
        + growth * 0.22
        + cashflow * 0.12
        + valuation * 0.10
        + industry * 0.14
        + trend * 0.10
        + factors["realtime_quality"] * 0.12
    )
    return round(score, 2), factors


def _score_ge(value: Any, bands: tuple[tuple[float, float], ...]) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    result = 0.0
    for threshold, score in bands:
        if number >= threshold:
            result = score
    return result


def _valuation_score(pe_ttm: Any, pb: Any) -> float:
    try:
        pe = float(pe_ttm)
        pb = float(pb)
    except (TypeError, ValueError):
        return 45.0
    if pe <= 0 or pb <= 0:
        return 25.0
    if pe <= 35 and pb <= 5:
        return 85.0
    if pe <= 60 and pb <= 8:
        return 65.0
    return 40.0


def _industry_score(industry: str) -> float:
    normalized = industry.lower()
    return 90.0 if any(item.lower() in normalized for item in settings.quality_growth_industries) else 55.0


def _reasoning(profile: dict[str, Any], factors: dict[str, float]) -> str:
    values = profile.get("fundamentals") or {}
    return (
        f"{profile.get('industry') or '未分类'}：ROE {values.get('roe')}, "
        f"营收同比 {values.get('revenue_yoy')}%，净利同比 {values.get('profit_yoy')}%，"
        f"质量综合分 {factors['realtime_quality']:.0f}"
    )
