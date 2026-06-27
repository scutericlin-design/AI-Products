from __future__ import annotations

from datetime import time

from app.services.timezone import now_beijing
from app.trading.types import MarketState, Quote


class MarketStateEngine:
    def evaluate(self, quotes: list[Quote]) -> MarketState:
        phase = self.current_phase()
        if not quotes:
            return MarketState(
                phase=phase,
                regime="no_data",
                breadth=0,
                avg_pct_change=0,
                total_amount_yi=0,
                quote_count=0,
                note="No quote data available.",
            )

        quote_count = len(quotes)
        breadth = sum(1 for quote in quotes if quote.pct_change > 0) / quote_count
        avg_pct = sum(quote.pct_change for quote in quotes) / quote_count
        total_amount_yi = sum(max(quote.amount_yi, 0) for quote in quotes)

        if breadth >= 0.65 and avg_pct >= 0.8:
            regime = "risk_on"
            note = "Broad strength with positive momentum."
        elif breadth <= 0.35 and avg_pct <= -0.5:
            regime = "risk_off"
            note = "Weak breadth and negative momentum."
        elif total_amount_yi < 20:
            regime = "thin_liquidity"
            note = "Liquidity is below the realtime decision threshold."
        else:
            regime = "neutral"
            note = "Mixed market state."

        return MarketState(
            phase=phase,
            regime=regime,
            breadth=round(breadth, 4),
            avg_pct_change=round(avg_pct, 4),
            total_amount_yi=round(total_amount_yi, 4),
            quote_count=quote_count,
            note=note,
        )

    def current_phase(self) -> str:
        current = now_beijing().time()
        if time(9, 15) <= current < time(9, 30):
            return "pre_open"
        if time(9, 30) <= current <= time(11, 30):
            return "morning_session"
        if time(11, 30) < current < time(13, 0):
            return "lunch_break"
        if time(13, 0) <= current <= time(15, 0):
            return "afternoon_session"
        if time(15, 0) < current <= time(15, 30):
            return "post_close"
        return "closed"

