from __future__ import annotations

from typing import Any

from app.config import settings
from data.tushare_client import Quote
from learning.strategy_params import param_float


def get_leader(quotes: list[Quote] | None = None) -> dict[str, Any]:
    if quotes is None:
        from realtime.market_stream import MarketStream

        quotes = MarketStream().latest_quotes()

    candidates = _rank_candidates(quotes)
    if not candidates:
        return {
            "stock": None,
            "strength": 0,
            "status": "none",
            "source": "no_data",
            "reason": "No leader candidate available.",
            "candidates": [],
        }

    leader = candidates[0]
    return {
        "stock": leader["symbol"],
        "name": leader["name"],
        "strength": leader["strength"],
        "status": leader["status"],
        "price": leader["price"],
        "pct_change": leader["pct_change"],
        "amount_yi": leader["amount_yi"],
        "source": "tushare" if leader["source"].startswith("tushare") else "dry_run",
        "candidates": candidates,
    }


class LeaderTracker:
    def track(self, quotes: list[Quote]) -> dict[str, Any]:
        return get_leader(quotes)


def _rank_candidates(quotes: list[Quote]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    min_turnover_yi = param_float("MIN_TURNOVER_YI", settings.min_turnover_yi)
    for quote in quotes:
        if quote.amount_yi < min_turnover_yi:
            continue
        strength = _strength(quote)
        if strength >= 85:
            status = "strong"
        elif strength >= 65:
            status = "watch"
        else:
            status = "weak"
        candidates.append(
            {
                "symbol": quote.symbol,
                "name": quote.name,
                "strength": strength,
                "status": status,
                "price": quote.price,
                "pct_change": quote.pct_change,
                "amount_yi": quote.amount_yi,
                "volume_ratio": quote.volume_ratio,
                "source": quote.source,
            }
        )
    candidates.sort(key=lambda item: item["strength"], reverse=True)
    return candidates[: settings.max_candidates]


def _strength(quote: Quote) -> int:
    momentum = max(min(quote.pct_change, 10), -10) * 4
    liquidity = min(quote.amount_yi, 20) * 1.5
    volume = min(quote.volume_ratio, 3) * 8
    return max(0, min(100, int(45 + momentum + liquidity + volume)))
