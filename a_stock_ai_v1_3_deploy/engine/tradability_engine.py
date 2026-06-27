from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.config import settings
from data.tushare_client import Quote
from learning.strategy_params import param_float


@dataclass(frozen=True)
class TradabilityResult:
    symbol: str
    name: str
    can_buy: bool
    score: float
    current_price: float
    buy_range_low: float
    buy_range_high: float
    max_buy_price: float
    stop_loss: float
    limit_up_price: float | None
    limit_pct: float
    is_limit_up: bool
    near_limit_up: bool
    reasons: list[str]


def evaluate_tradability(quote: Quote) -> dict[str, Any]:
    if quote.price <= 0:
        return asdict(
            TradabilityResult(
                symbol=quote.symbol,
                name=quote.name,
                can_buy=False,
                score=0.0,
                current_price=quote.price,
                buy_range_low=0.0,
                buy_range_high=0.0,
                max_buy_price=0.0,
                stop_loss=0.0,
                limit_up_price=None,
                limit_pct=_limit_pct(quote),
                is_limit_up=False,
                near_limit_up=False,
                reasons=["价格无效，不能推荐"],
            )
        )

    limit_pct = _limit_pct(quote)
    limit_up_price = _limit_up_price(quote, limit_pct)
    near_limit_threshold = limit_up_price * (1 - settings.near_limit_up_buffer) if limit_up_price else 0.0
    is_limit_up = bool(limit_up_price and quote.price >= limit_up_price - 0.01)
    near_limit_up = bool(limit_up_price and quote.price >= near_limit_threshold)

    buy_range_pullback_pct = param_float("BUY_RANGE_PULLBACK_PCT", settings.buy_range_pullback_pct)
    max_chase_pct = param_float("MAX_CHASE_PCT", settings.max_chase_pct)
    stop_loss_pct = param_float("STOP_LOSS_PCT", settings.stop_loss_pct)
    min_turnover_yi = param_float("MIN_TURNOVER_YI", settings.min_turnover_yi)
    max_recommend_pct_change = param_float("MAX_RECOMMEND_PCT_CHANGE", settings.max_recommend_pct_change)

    buy_range_low = round(quote.price * (1 - buy_range_pullback_pct), 2)
    max_by_chase = quote.price * (1 + max_chase_pct)
    max_by_limit = limit_up_price * (1 - settings.near_limit_up_buffer) if limit_up_price else max_by_chase
    buy_range_high = round(max(0.0, min(max_by_chase, max_by_limit)), 2)
    max_buy_price = buy_range_high
    stop_loss = round(quote.price * (1 - stop_loss_pct), 2)

    reasons: list[str] = []
    can_buy = True
    if quote.amount_yi < min_turnover_yi:
        can_buy = False
        reasons.append(f"成交额不足{min_turnover_yi:.1f}亿，流动性不够")
    if quote.pct_change >= max_recommend_pct_change:
        can_buy = False
        reasons.append(f"涨幅{quote.pct_change:.2f}%过高，追价性价比不足")
    if is_limit_up:
        can_buy = False
        reasons.append("已经涨停，买入可成交性差")
    elif near_limit_up:
        can_buy = False
        reasons.append("接近涨停，容易排队买不到")
    if quote.ask_price and quote.ask_price > max_buy_price:
        can_buy = False
        reasons.append("卖一价已高于追价上限")
    if buy_range_high <= 0 or buy_range_low > buy_range_high:
        can_buy = False
        reasons.append("买入区间无效")

    score = _score(quote, can_buy, near_limit_up, is_limit_up)
    if can_buy:
        reasons.append("未触及涨停过滤，且仍在可成交观察区")

    return asdict(
        TradabilityResult(
            symbol=quote.symbol,
            name=quote.name,
            can_buy=can_buy,
            score=round(score, 2),
            current_price=round(quote.price, 2),
            buy_range_low=buy_range_low,
            buy_range_high=buy_range_high,
            max_buy_price=max_buy_price,
            stop_loss=stop_loss,
            limit_up_price=round(limit_up_price, 2) if limit_up_price else None,
            limit_pct=limit_pct,
            is_limit_up=is_limit_up,
            near_limit_up=near_limit_up,
            reasons=reasons,
        )
    )


class TradabilityEngine:
    def evaluate(self, quote: Quote) -> dict[str, Any]:
        return evaluate_tradability(quote)


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


def _limit_up_price(quote: Quote, limit_pct: float) -> float | None:
    if quote.pre_close <= 0:
        return None
    return quote.pre_close * (1 + limit_pct / 100)


def _score(quote: Quote, can_buy: bool, near_limit_up: bool, is_limit_up: bool) -> float:
    min_turnover_yi = param_float("MIN_TURNOVER_YI", settings.min_turnover_yi)
    max_recommend_pct_change = param_float("MAX_RECOMMEND_PCT_CHANGE", settings.max_recommend_pct_change)
    score = 50.0
    score += min(max(quote.amount_yi - min_turnover_yi, 0), 12) * 2.0
    score += min(max(quote.volume_ratio - 1, 0), 2) * 8.0
    score += max(0.0, min(quote.pct_change, 5.5)) * 3.0
    if quote.pct_change > max_recommend_pct_change:
        score -= 25.0
    if near_limit_up:
        score -= 30.0
    if is_limit_up:
        score -= 60.0
    if not can_buy:
        score -= 20.0
    return max(0.0, min(100.0, score))
