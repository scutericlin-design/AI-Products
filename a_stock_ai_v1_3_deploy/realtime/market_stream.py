from __future__ import annotations

from collections import Counter

from app.config import settings
from data.stock_universe import StockUniverseManager, StockUniverseSnapshot
from data.tushare_client import Quote, TushareClient


class MarketStream:
    def __init__(
        self,
        client: TushareClient | None = None,
        universe_manager: StockUniverseManager | None = None,
    ) -> None:
        self.client = client or TushareClient()
        self.universe_manager = universe_manager or StockUniverseManager(self.client)

    def latest_quotes(self) -> list[Quote]:
        snapshot = self.universe_manager.resolve()
        return self.client.fetch_realtime_quotes(snapshot.symbols)

    def quotes_for_symbols(self, symbols: list[str]) -> list[Quote]:
        return self.client.fetch_realtime_quotes(symbols)

    def market_sentiment_quotes(self) -> list[Quote]:
        """Compatibility path for callers that need the sentiment basket only."""
        return self.client.fetch_market_sentiment_quotes()

    def load_cycle_quotes(self) -> tuple[list[Quote], list[Quote], dict[str, object]]:
        """Fetch selection and sentiment quotes through one deduplicated provider request."""
        snapshot = self.universe_manager.resolve()
        sentiment_symbols = _dedupe(
            [self.client.normalize_symbol(symbol) for symbol in settings.market_sentiment_symbols]
        )
        requested_symbols = _dedupe(snapshot.symbols + sentiment_symbols)
        all_quotes = self.client.fetch_realtime_quotes(requested_symbols)
        by_symbol = {
            self.client.normalize_symbol(quote.symbol): quote
            for quote in all_quotes
            if quote.price > 0
        }
        selection_quotes = [
            by_symbol[symbol]
            for symbol in snapshot.symbols
            if symbol in by_symbol
        ]
        sentiment_quotes = [
            by_symbol[symbol]
            for symbol in sentiment_symbols
            if symbol in by_symbol
        ]
        return selection_quotes, sentiment_quotes, self._selection_audit(
            snapshot,
            received_count=len(selection_quotes),
            total_requested_count=len(requested_symbols),
            sentiment_requested_count=len(sentiment_symbols),
        )

    def selection_universe(self, received_count: int) -> dict[str, object]:
        """Expose the current live universe without triggering a realtime quote request."""
        return self._selection_audit(self.universe_manager.resolve(), received_count=received_count)

    def _selection_audit(
        self,
        snapshot: StockUniverseSnapshot,
        received_count: int,
        total_requested_count: int | None = None,
        sentiment_requested_count: int | None = None,
    ) -> dict[str, object]:
        requested_count = len(snapshot.symbols)
        tag_counts = Counter(tag for tags in snapshot.tags.values() for tag in tags)
        comparable = (
            snapshot.profile == "adaptive"
            and snapshot.limit == 160
            and snapshot.source == "tushare_daily_basic"
            and snapshot.cache_state not in {"cold_start_fallback", "stale_fallback"}
        )
        audit: dict[str, object] = {
            "asset_class": "stock",
            "profile": snapshot.profile,
            "configured_limit": snapshot.limit,
            "requested_count": requested_count,
            "received_count": received_count,
            "coverage_ratio": round(received_count / requested_count, 4) if requested_count else 0.0,
            "as_of_date": snapshot.as_of_date,
            "source": snapshot.source,
            "cache_state": snapshot.cache_state,
            "updated_at": snapshot.updated_at,
            "fallback_reason": snapshot.fallback_reason,
            "symbols_fingerprint": snapshot.fingerprint,
            "symbols_sample": snapshot.symbols[:12],
            "tag_counts": dict(sorted(tag_counts.items())),
            "comparison_backtest_profile": "adaptive",
            "comparable_to_adaptive_160_backtest": comparable,
        }
        if total_requested_count is not None:
            audit["combined_quote_request_count"] = total_requested_count
        if sentiment_requested_count is not None:
            audit["sentiment_requested_count"] = sentiment_requested_count
            audit["selection_sentiment_overlap_count"] = (
                requested_count + sentiment_requested_count - int(total_requested_count or 0)
            )
        return audit


def _dedupe(symbols: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        value = str(symbol).strip().upper()
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
