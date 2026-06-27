from __future__ import annotations

from app.config import settings
from app.trading.types import LeaderCandidate, Quote


class LeaderTracker:
    def select(self, quotes: list[Quote]) -> list[LeaderCandidate]:
        candidates: list[LeaderCandidate] = []
        for quote in quotes:
            if quote.amount_yi < settings.trading_min_turnover_yi:
                continue
            leader_score = self._leader_score(quote)
            candidates.append(
                LeaderCandidate(
                    symbol=quote.symbol,
                    name=quote.name,
                    price=quote.price,
                    pct_change=quote.pct_change,
                    amount_yi=quote.amount_yi,
                    leader_score=leader_score,
                    source=quote.source,
                    raw=quote.raw,
                )
            )
        candidates.sort(key=lambda item: item.leader_score, reverse=True)
        return candidates[: settings.trading_max_candidates]

    def _leader_score(self, quote: Quote) -> float:
        momentum = max(quote.pct_change, -5) * 2.0
        liquidity = min(quote.amount_yi / 10, 8)
        volume_ratio = min((quote.volume_ratio or 1.0) * 1.5, 4)
        return round(momentum + liquidity + volume_ratio, 4)

