from __future__ import annotations

from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from app.config import DEFAULT_MARKET_SENTIMENT_SYMBOLS, settings
from data.tushare_client import Quote, TushareClient
from engine.sentiment_engine import get_market_sentiment


class MarketSentimentSampleTests(TestCase):
    def test_broad_sample_is_fetched_in_bounded_batches(self) -> None:
        client = TushareClient()
        captured_batches: list[list[str]] = []

        def fake_fetch(batch: list[str]) -> list[Quote]:
            captured_batches.append(batch)
            return [
                Quote(
                    symbol=symbol,
                    name=symbol,
                    price=10.0,
                    pct_change=1.0,
                    amount_yi=3.0,
                    source="test_tushare",
                )
                for symbol in batch
            ]

        test_settings = replace(
            settings,
            dry_run=False,
            market_sentiment_symbols=list(DEFAULT_MARKET_SENTIMENT_SYMBOLS),
        )
        with patch("data.tushare_client.settings", test_settings), patch.object(
            client, "_fetch_quotes_for_symbols", side_effect=fake_fetch
        ):
            quotes = client.fetch_market_sentiment_quotes()

        self.assertEqual(len(quotes), len(DEFAULT_MARKET_SENTIMENT_SYMBOLS))
        self.assertGreaterEqual(len(captured_batches), 2)
        self.assertTrue(all(len(batch) <= 50 for batch in captured_batches))

    def test_broad_sample_reaches_high_coverage(self) -> None:
        quotes = [
            Quote(
                symbol=symbol,
                name=symbol,
                price=10.0,
                pct_change=0.5,
                amount_yi=3.0,
                source="test_tushare",
            )
            for symbol in DEFAULT_MARKET_SENTIMENT_SYMBOLS
        ]

        sentiment = get_market_sentiment(quotes, {"state": "SIDEWAYS"})

        self.assertGreaterEqual(sentiment["coverage_count"], 80)
        self.assertEqual(sentiment["coverage_level"], "high")
