from __future__ import annotations

from statistics import median
from typing import Any

from app.config import settings
from data.tushare_client import Quote
from learning.strategy_params import param_float, param_int


def get_market_sentiment(
    quotes: list[Quote] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not settings.sentiment_enabled:
        return _disabled_sentiment()

    if quotes is None:
        from realtime.market_stream import MarketStream

        quotes = MarketStream().latest_quotes()

    valid = [quote for quote in quotes or [] if quote.price > 0]
    if not valid:
        return _empty_sentiment(state)

    count = len(valid)
    pct_changes = [quote.pct_change for quote in valid]
    positive_count = sum(1 for value in pct_changes if value > 0)
    negative_count = sum(1 for value in pct_changes if value < 0)
    flat_count = count - positive_count - negative_count
    strong_up_count = sum(1 for value in pct_changes if value >= 3.0)
    strong_down_count = sum(1 for value in pct_changes if value <= -3.0)
    min_turnover_yi = param_float("MIN_TURNOVER_YI", settings.min_turnover_yi)
    active_count = sum(1 for quote in valid if quote.amount_yi >= min_turnover_yi)
    volume_active_count = sum(1 for quote in valid if quote.volume_ratio >= 1.2)

    limit_stats = _limit_stats(valid)
    breadth = positive_count / count
    negative_ratio = negative_count / count
    strong_up_ratio = strong_up_count / count
    strong_down_ratio = strong_down_count / count
    active_ratio = active_count / count
    volume_active_ratio = volume_active_count / count
    avg_pct_change = sum(pct_changes) / count
    median_pct_change = median(pct_changes)
    total_amount_yi = sum(max(quote.amount_yi, 0.0) for quote in valid)
    avg_amount_yi = total_amount_yi / count

    breadth_score = _clamp(50 + (breadth - 0.5) * 110)
    trend_score = _clamp(50 + avg_pct_change * 7 + median_pct_change * 4)
    liquidity_score = _clamp(35 + active_ratio * 35 + volume_active_ratio * 18 + min(avg_amount_yi, 8) * 2)
    limit_heat_score = _clamp(
        50
        + limit_stats["limit_up_ratio"] * 120
        + limit_stats["near_limit_up_ratio"] * 45
        - limit_stats["limit_down_ratio"] * 140
        - limit_stats["near_limit_down_ratio"] * 70
    )
    panic_score = _clamp(
        negative_ratio * 35
        + strong_down_ratio * 35
        + limit_stats["near_limit_down_ratio"] * 45
        + limit_stats["limit_down_ratio"] * 55
        + max(-avg_pct_change, 0.0) * 8
    )
    raw_risk_appetite_score = _clamp(
        breadth_score * 0.35
        + trend_score * 0.25
        + liquidity_score * 0.20
        + limit_heat_score * 0.20
        - panic_score * 0.25
    )

    coverage_score = _coverage_score(count)
    coverage_level = _coverage_level(count)
    coverage_weight = max(0.35, min(1.0, (coverage_score / 100) ** 0.5))
    sentiment_score = _clamp(50 + (raw_risk_appetite_score - 50) * coverage_weight)

    market_state = str((state or {}).get("state") or "").upper()
    if market_state == "DOWNTREND":
        sentiment_score = min(sentiment_score, 44.0)
    elif market_state == "UPTREND" and sentiment_score >= 54 and panic_score < 45:
        sentiment_score = min(100.0, sentiment_score + 3.0)

    risk_appetite = _risk_appetite(sentiment_score, panic_score)
    trade_permission = _trade_permission(
        sentiment_score=sentiment_score,
        panic_score=panic_score,
        breadth=breadth,
        avg_pct_change=avg_pct_change,
        coverage_level=coverage_level,
        risk_appetite=risk_appetite,
    )
    flags = _flags(
        coverage_level=coverage_level,
        breadth=breadth,
        avg_pct_change=avg_pct_change,
        panic_score=panic_score,
        risk_appetite=risk_appetite,
        trade_permission=trade_permission,
        limit_stats=limit_stats,
    )

    return {
        "source": "realtime_quote_sentiment",
        "sentiment_score": round(sentiment_score, 2),
        "raw_score": round(raw_risk_appetite_score, 2),
        "sentiment_status": _sentiment_status(sentiment_score, panic_score),
        "risk_appetite": risk_appetite,
        "trade_permission": trade_permission,
        "panic_score": round(panic_score, 2),
        "coverage_score": round(coverage_score, 2),
        "coverage_level": coverage_level,
        "coverage_count": count,
        "breadth": round(breadth, 4),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "flat_count": flat_count,
        "strong_up_count": strong_up_count,
        "strong_down_count": strong_down_count,
        "limit_up_count": limit_stats["limit_up_count"],
        "near_limit_up_count": limit_stats["near_limit_up_count"],
        "limit_down_count": limit_stats["limit_down_count"],
        "near_limit_down_count": limit_stats["near_limit_down_count"],
        "avg_pct_change": round(avg_pct_change, 4),
        "median_pct_change": round(median_pct_change, 4),
        "active_ratio": round(active_ratio, 4),
        "volume_active_ratio": round(volume_active_ratio, 4),
        "total_amount_yi": round(total_amount_yi, 4),
        "factor_scores": {
            "breadth": round(breadth_score, 2),
            "trend": round(trend_score, 2),
            "liquidity": round(liquidity_score, 2),
            "limit_heat": round(limit_heat_score, 2),
            "risk_appetite": round(raw_risk_appetite_score, 2),
        },
        "flags": flags,
        "hard_rules": _hard_rules(trade_permission, panic_score, coverage_level),
    }


class SentimentEngine:
    def evaluate(
        self,
        quotes: list[Quote],
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return get_market_sentiment(quotes, state)


def _disabled_sentiment() -> dict[str, Any]:
    return {
        "source": "disabled",
        "sentiment_score": 50.0,
        "raw_score": 50.0,
        "sentiment_status": "neutral",
        "risk_appetite": "neutral",
        "trade_permission": "BUY_ALLOWED",
        "panic_score": 0.0,
        "coverage_score": 0.0,
        "coverage_level": "disabled",
        "coverage_count": 0,
        "breadth": 0.5,
        "flags": ["sentiment_disabled"],
        "hard_rules": [],
    }


def _empty_sentiment(state: dict[str, Any] | None) -> dict[str, Any]:
    market_state = str((state or {}).get("state") or "NO_DATA").upper()
    return {
        "source": "no_data",
        "sentiment_score": 0.0,
        "raw_score": 0.0,
        "sentiment_status": "no_data",
        "risk_appetite": "risk_off",
        "trade_permission": "NO_BUY",
        "panic_score": 100.0 if market_state == "DOWNTREND" else 0.0,
        "coverage_score": 0.0,
        "coverage_level": "none",
        "coverage_count": 0,
        "breadth": 0.0,
        "positive_count": 0,
        "negative_count": 0,
        "flat_count": 0,
        "strong_up_count": 0,
        "strong_down_count": 0,
        "limit_up_count": 0,
        "near_limit_up_count": 0,
        "limit_down_count": 0,
        "near_limit_down_count": 0,
        "avg_pct_change": 0.0,
        "median_pct_change": 0.0,
        "active_ratio": 0.0,
        "volume_active_ratio": 0.0,
        "total_amount_yi": 0.0,
        "factor_scores": {
            "breadth": 0.0,
            "trend": 0.0,
            "liquidity": 0.0,
            "limit_heat": 0.0,
            "risk_appetite": 0.0,
        },
        "flags": ["sentiment_no_data"],
        "hard_rules": ["没有行情数据时禁止新增买入"],
    }


def _limit_stats(quotes: list[Quote]) -> dict[str, Any]:
    limit_up_count = 0
    near_limit_up_count = 0
    limit_down_count = 0
    near_limit_down_count = 0
    for quote in quotes:
        if quote.pre_close <= 0:
            continue
        limit_pct = _limit_pct(quote)
        limit_up_price = quote.pre_close * (1 + limit_pct / 100)
        limit_down_price = quote.pre_close * (1 - limit_pct / 100)
        near_up_threshold = limit_up_price * (1 - settings.near_limit_up_buffer)
        near_down_threshold = limit_down_price * (1 + settings.near_limit_up_buffer)
        if quote.price >= limit_up_price - 0.01:
            limit_up_count += 1
        elif quote.price >= near_up_threshold:
            near_limit_up_count += 1
        if quote.price <= limit_down_price + 0.01:
            limit_down_count += 1
        elif quote.price <= near_down_threshold:
            near_limit_down_count += 1
    count = max(len(quotes), 1)
    return {
        "limit_up_count": limit_up_count,
        "near_limit_up_count": near_limit_up_count,
        "limit_down_count": limit_down_count,
        "near_limit_down_count": near_limit_down_count,
        "limit_up_ratio": limit_up_count / count,
        "near_limit_up_ratio": near_limit_up_count / count,
        "limit_down_ratio": limit_down_count / count,
        "near_limit_down_ratio": near_limit_down_count / count,
    }


def _coverage_score(count: int) -> float:
    full_coverage_count = param_int("SENTIMENT_FULL_COVERAGE_COUNT", settings.sentiment_full_coverage_count)
    return min(100.0, count / max(full_coverage_count, 1) * 100)


def _coverage_level(count: int) -> str:
    full_coverage_count = param_int("SENTIMENT_FULL_COVERAGE_COUNT", settings.sentiment_full_coverage_count)
    low_coverage_count = param_int("SENTIMENT_LOW_COVERAGE_COUNT", settings.sentiment_low_coverage_count)
    if count >= full_coverage_count:
        return "high"
    if count >= low_coverage_count:
        return "medium"
    return "low"


def _risk_appetite(sentiment_score: float, panic_score: float) -> str:
    panic_threshold = param_float("SENTIMENT_PANIC_THRESHOLD", settings.sentiment_panic_threshold)
    risk_off_score = param_float("SENTIMENT_RISK_OFF_SCORE", settings.sentiment_risk_off_score)
    if panic_score >= panic_threshold:
        return "panic"
    if sentiment_score >= 65 and panic_score < 45:
        return "risk_on"
    if sentiment_score < risk_off_score or panic_score >= 55:
        return "risk_off"
    return "neutral"


def _sentiment_status(sentiment_score: float, panic_score: float) -> str:
    panic_threshold = param_float("SENTIMENT_PANIC_THRESHOLD", settings.sentiment_panic_threshold)
    min_buy_score = param_float("SENTIMENT_MIN_BUY_SCORE", settings.sentiment_min_buy_score)
    if panic_score >= panic_threshold:
        return "panic"
    if sentiment_score >= 65:
        return "hot"
    if sentiment_score >= min_buy_score:
        return "constructive"
    if sentiment_score >= 45:
        return "neutral"
    return "weak"


def _trade_permission(
    sentiment_score: float,
    panic_score: float,
    breadth: float,
    avg_pct_change: float,
    coverage_level: str,
    risk_appetite: str,
) -> str:
    min_buy_score = param_float("SENTIMENT_MIN_BUY_SCORE", settings.sentiment_min_buy_score)
    risk_off_score = param_float("SENTIMENT_RISK_OFF_SCORE", settings.sentiment_risk_off_score)
    panic_threshold = param_float("SENTIMENT_PANIC_THRESHOLD", settings.sentiment_panic_threshold)
    if (
        panic_score >= panic_threshold
        or sentiment_score < risk_off_score
        or (breadth <= 0.25 and avg_pct_change <= -0.8)
        or risk_appetite == "panic"
    ):
        return "NO_BUY"
    if (
        sentiment_score < min_buy_score
        or panic_score >= 55
        or breadth < 0.42
        or risk_appetite == "risk_off"
    ):
        return "LIGHT_ONLY"
    return "BUY_ALLOWED"


def _flags(
    coverage_level: str,
    breadth: float,
    avg_pct_change: float,
    panic_score: float,
    risk_appetite: str,
    trade_permission: str,
    limit_stats: dict[str, Any],
) -> list[str]:
    flags: list[str] = []
    if coverage_level == "low":
        flags.append("low_sentiment_coverage")
    if breadth < 0.42:
        flags.append("weak_breadth")
    elif breadth >= 0.62:
        flags.append("broad_strength")
    if avg_pct_change <= -0.8:
        flags.append("negative_average_return")
    if panic_score >= 55:
        flags.append("panic_pressure")
    if risk_appetite in {"risk_off", "panic"}:
        flags.append(f"appetite_{risk_appetite}")
    if trade_permission == "NO_BUY":
        flags.append("sentiment_no_buy")
    elif trade_permission == "LIGHT_ONLY":
        flags.append("sentiment_light_only")
    if limit_stats["near_limit_up_ratio"] + limit_stats["limit_up_ratio"] >= 0.18:
        flags.append("limit_up_pressure")
    if limit_stats["near_limit_down_ratio"] + limit_stats["limit_down_ratio"] >= 0.08:
        flags.append("limit_down_pressure")
    return flags or ["sentiment_normal"]


def _hard_rules(trade_permission: str, panic_score: float, coverage_level: str) -> list[str]:
    panic_threshold = param_float("SENTIMENT_PANIC_THRESHOLD", settings.sentiment_panic_threshold)
    rules: list[str] = []
    if trade_permission == "NO_BUY":
        rules.append("市场情绪禁止新增买入")
    if trade_permission == "LIGHT_ONLY":
        rules.append("市场情绪只允许轻仓试错，必须满足更高个股分数")
    if panic_score >= panic_threshold:
        rules.append("恐慌分达到阈值，强制转防守")
    if coverage_level == "low":
        rules.append("情绪样本覆盖不足，系统自动降低情绪权重和仓位")
    return rules


def _limit_pct(quote: Quote) -> float:
    name = quote.name.upper()
    symbol = quote.symbol.upper()
    if name.startswith(("ST", "*ST")) or " ST" in name:
        return 5.0
    if symbol.endswith(".BJ"):
        return 30.0
    if symbol.startswith(("300", "688")):
        return 20.0
    return 10.0


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))
