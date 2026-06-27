from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from data.tushare_client import Quote


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def get_market_state(quotes: list[Quote] | None = None) -> dict[str, Any]:
    if quotes is None:
        from realtime.market_stream import MarketStream

        quotes = MarketStream().latest_quotes()

    if not quotes:
        return {
            "state": "NO_DATA",
            "sentiment": 0,
            "volume": "none",
            "phase": _phase(),
            "breadth": 0.0,
            "avg_pct_change": 0.0,
            "total_amount_yi": 0.0,
            "quote_count": 0,
            "source": "no_data",
            "reason": "No realtime quotes available; perception must stay neutral.",
        }

    breadth = sum(1 for item in quotes if item.pct_change > 0) / len(quotes)
    avg_pct_change = sum(item.pct_change for item in quotes) / len(quotes)
    total_amount_yi = sum(max(item.amount_yi, 0) for item in quotes)

    if breadth >= 0.65 and avg_pct_change >= 0.8:
        state = "UPTREND"
        sentiment = min(95, int(60 + breadth * 30 + avg_pct_change * 2))
    elif breadth <= 0.35 and avg_pct_change <= -0.5:
        state = "DOWNTREND"
        sentiment = max(5, int(40 + breadth * 20 + avg_pct_change * 2))
    else:
        state = "SIDEWAYS"
        sentiment = int(50 + (breadth - 0.5) * 40 + avg_pct_change)

    if total_amount_yi >= 30:
        volume = "high"
    elif total_amount_yi >= 10:
        volume = "normal"
    else:
        volume = "low"

    return {
        "state": state,
        "sentiment": max(0, min(sentiment, 100)),
        "volume": volume,
        "phase": _phase(),
        "breadth": round(breadth, 4),
        "avg_pct_change": round(avg_pct_change, 4),
        "total_amount_yi": round(total_amount_yi, 4),
        "quote_count": len(quotes),
        "source": "tushare" if any(item.source.startswith("tushare") for item in quotes) else "dry_run",
    }


class MarketStateEngine:
    def evaluate(self, quotes: list[Quote]) -> dict[str, Any]:
        return get_market_state(quotes)


def _phase() -> str:
    now = datetime.now(BEIJING_TZ).time()
    if time(9, 15) <= now < time(9, 30):
        return "pre_open"
    if time(9, 30) <= now <= time(11, 30):
        return "morning"
    if time(11, 30) < now < time(13, 0):
        return "lunch_break"
    if time(13, 0) <= now <= time(15, 0):
        return "afternoon"
    if time(15, 0) < now <= time(15, 30):
        return "post_close"
    return "closed"
