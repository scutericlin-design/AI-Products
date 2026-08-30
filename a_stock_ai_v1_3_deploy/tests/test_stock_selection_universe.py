from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import pandas as pd

from app.config import settings
from data.stock_universe import StockUniverseManager, StockUniverseSnapshot, build_stock_universe
from data.tushare_client import Quote, TushareClient
from realtime.market_stream import MarketStream


class _DailyBasicClient:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.fetch_count = 0
        self._normalizer = TushareClient()

    def normalize_symbol(self, symbol: str) -> str:
        return self._normalizer.normalize_symbol(symbol)

    def core_selection_symbols(self) -> list[str]:
        return ["000001.SZ", "600519.SH", "300750.SZ", "002594.SZ", "688981.SH"]

    def _default_symbols(self) -> list[str]:
        return self.core_selection_symbols()

    def fetch_daily_basic_snapshot(self, as_of_date: str) -> tuple[pd.DataFrame, str]:
        self.fetch_count += 1
        return self.frame.copy(), "20260807"


class _FixedUniverseManager:
    def __init__(self, snapshot: StockUniverseSnapshot) -> None:
        self.snapshot = snapshot

    def resolve(self) -> StockUniverseSnapshot:
        return self.snapshot


class _QuoteClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._normalizer = TushareClient()

    def normalize_symbol(self, symbol: str) -> str:
        return self._normalizer.normalize_symbol(symbol)

    def fetch_realtime_quotes(self, symbols: list[str]) -> list[Quote]:
        self.calls.append(list(symbols))
        return [
            Quote(
                symbol=symbol,
                name=symbol,
                price=10.0,
                pct_change=1.0,
                amount_yi=3.0,
                source="test",
            )
            for symbol in symbols
        ]

    def fetch_market_sentiment_quotes(self) -> list[Quote]:
        raise AssertionError("combined intraday fetch must not issue a second sentiment request")


def _daily_basic_frame() -> pd.DataFrame:
    rows: list[dict[str, float | str]] = []
    for index in range(56):
        rows.append(
            {
                "ts_code": f"{601000 + index}.SH",
                "turnover_rate": 0.5,
                "circ_mv": 20_000_000 + index,
            }
        )
    for index in range(120):
        rows.append(
            {
                "ts_code": f"{2000 + index:06d}.SZ",
                "turnover_rate": 2.0,
                "circ_mv": 1_000_000 + index,
            }
        )
    return pd.DataFrame(rows)


class StockSelectionUniverseTests(TestCase):
    def test_adaptive_builder_keeps_the_historical_core_large_active_composition(self) -> None:
        core = ["000001.SZ", "600519.SH", "300750.SZ", "002594.SZ", "688981.SH"]
        universe, tags = build_stock_universe(
            _daily_basic_frame(),
            profile_name="adaptive",
            limit=160,
            core_symbols=core,
            normalize_symbol=TushareClient().normalize_symbol,
        )

        self.assertEqual(len(universe), 160)
        self.assertEqual(universe[:5], core)
        self.assertEqual(sum("core" in values for values in tags.values()), 5)
        self.assertEqual(sum("large" in values for values in tags.values()), 56)
        self.assertEqual(sum("active" in values for values in tags.values()), 99)

    def test_daily_basic_universe_is_cached_for_intraday_cycles(self) -> None:
        client = _DailyBasicClient(_daily_basic_frame())
        with TemporaryDirectory() as temp_dir:
            test_settings = replace(
                settings,
                stock_selection_universe_profile="adaptive",
                stock_selection_universe_limit=160,
                stock_selection_universe_cache_path=Path(temp_dir) / "universe.json",
                stock_selection_universe_cache_hours=36,
            )
            with patch("data.stock_universe.settings", test_settings):
                manager = StockUniverseManager(client)
                first = manager.resolve()
                second = manager.resolve()

        self.assertEqual(first.cache_state, "refreshed")
        self.assertEqual(second.cache_state, "hit")
        self.assertEqual(len(first.symbols), 160)
        self.assertEqual(client.fetch_count, 1)

    def test_cycle_uses_one_deduplicated_request_for_selection_and_sentiment(self) -> None:
        selection_symbols = ["000001.SZ", "600519.SH", "300750.SZ"]
        snapshot = StockUniverseSnapshot(
            symbols=selection_symbols,
            tags={symbol: ["core"] for symbol in selection_symbols},
            profile="adaptive",
            limit=160,
            as_of_date="20260807",
            source="tushare_daily_basic",
            cache_state="hit",
            updated_at="2026-08-08T09:30:00+08:00",
        )
        client = _QuoteClient()
        test_settings = replace(
            settings,
            market_sentiment_symbols=["000001.SZ", "000002.SZ", "000003.SZ"],
        )
        with patch("realtime.market_stream.settings", test_settings):
            selection, sentiment, audit = MarketStream(
                client=client,
                universe_manager=_FixedUniverseManager(snapshot),
            ).load_cycle_quotes()

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0], ["000001.SZ", "600519.SH", "300750.SZ", "000002.SZ", "000003.SZ"])
        self.assertEqual(len(selection), 3)
        self.assertEqual(len(sentiment), 3)
        self.assertEqual(audit["combined_quote_request_count"], 5)
        self.assertTrue(audit["comparable_to_adaptive_160_backtest"])
