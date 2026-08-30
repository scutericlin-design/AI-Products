from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from etf_strategy.config import ETFStrategySettings
from etf_strategy.file_universe import DEFENSIVE_ETF_SYMBOL, FILE_ETF_SYMBOLS
from etf_strategy.live_runner import ETFMinutePaperRunner
from etf_strategy.models import ETFBar, ETFInstrument
from etf_strategy.storage import ETFLocalStore
from etf_strategy.wufu_v7_static import WUFU_INDEX_SYMBOLS


BEIJING = ZoneInfo("Asia/Shanghai")


class _SourceMinuteClient:
    def fetch_today_minutes(self, symbol: str) -> pd.DataFrame:
        start = pd.Timestamp("2026-07-27 12:40:00")
        if symbol == "510300.SH":
            price = 3.8
            step = 0.0001
        elif symbol in {"512100.SH", "510500.SH"}:
            start = pd.Timestamp("2026-07-27 09:30:00")
            price = 1.0
            step = 0.0001
        else:
            price = 1.2
            step = 0.0003
        frame = pd.DataFrame(
            {
                "time": [(start + timedelta(minutes=index)).strftime("%H:%M:%S") for index in range(190)],
                "close": [price + step * index for index in range(190)],
            }
        )
        frame.attrs["source"] = "test:source_minutes"
        return frame


class _BatchSourceClient:
    def __init__(self) -> None:
        self.direct_calls = 0

    def fetch_realtime_spot_minutes(self, symbols: set[str], captured_at: datetime) -> dict[str, pd.DataFrame]:
        frames: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            price = 1.2 if symbol == "513100.SH" else 1.0
            frame = pd.DataFrame(
                [{"trade_time": captured_at.replace(second=0, microsecond=0, tzinfo=None), "close": price}]
            )
            frame.attrs["source"] = "test:batch_spot"
            frames[symbol] = frame
        return frames

    def fetch_today_minutes(self, symbol: str) -> pd.DataFrame:
        self.direct_calls += 1
        return pd.DataFrame()


class _BootstrapSourceClient:
    def __init__(self) -> None:
        self.daily_history_calls: list[str] = []
        self.index_history_calls: list[str] = []

    def fetch_open_trade_dates(self, start_date: str, end_date: str) -> list[str]:
        return [item.strftime("%Y%m%d") for item in pd.bdate_range(start_date, end_date)]

    def fetch_daily_snapshot(self, trade_date: str) -> tuple[list[ETFBar], str]:
        return [], "test:empty"

    def fetch_daily_history(self, symbol: str, start_date: str, end_date: str) -> list[ETFBar]:
        self.daily_history_calls.append(symbol)
        bars: list[ETFBar] = []
        close = 1.0
        for value in pd.bdate_range(start_date, end_date):
            close *= 1.001
            bars.append(_bar(symbol, value.strftime("%Y%m%d"), close))
        return bars

    def fetch_realtime_spot_daily(self, symbols: set[str], trade_date: str) -> list[ETFBar]:
        return []

    def fetch_index_daily(self, symbol: str, start_date: str, end_date: str) -> list[dict[str, object]]:
        return []

    def fetch_index_daily_akshare(self, symbol: str, start_date: str, end_date: str) -> list[dict[str, object]]:
        self.index_history_calls.append(symbol)
        close = 1000.0
        rows: list[dict[str, object]] = []
        for value in pd.bdate_range(start_date, end_date):
            close *= 1.001
            rows.append({"symbol": symbol, "trade_date": value.strftime("%Y%m%d"), "close": close})
        return rows


class SourceFileLiveRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = ETFStrategySettings(
            project_root=root,
            storage_dir=root,
            db_path=root / "etf.sqlite",
            reports_dir=root / "reports",
            tushare_token=None,
            tushare_base_url=None,
            minimax_api_key=None,
            minimax_endpoint=None,
            minimax_model="test",
            ai_enabled=True,
            request_pause_seconds=0.0,
            daily_amount_multiplier=1000.0,
            min_avg_turnover_yuan=0.0,
            turnover_to_order_multiple=1.0,
            max_nav_premium_pct=0.03,
            minute_initial_cash=100_000.0,
            minute_max_position_weight=0.05,
            minute_slippage_rate=0.005,
            minute_commission_rate=0.005,
            minute_min_commission=20.0,
            minute_strategy_profile="wufu_v7_static",
        )
        self.store = ETFLocalStore(self.settings.db_path)
        self.store.upsert_universe(
            [
                ETFInstrument(symbol="510300.SH", name="沪深300ETF"),
                ETFInstrument(symbol="513100.SH", name="纳指ETF"),
            ]
        )
        dates = _business_dates("2026-06-12", 31)
        bars = []
        for symbol, rate, start in (("510300.SH", 0.001, 3.2), ("513100.SH", 0.003, 1.0)):
            close = start
            for trade_date in dates:
                close *= 1 + rate
                bars.append(_bar(symbol, trade_date, close))
        self.store.upsert_daily_bars(bars)
        index_rows = []
        for symbol in WUFU_INDEX_SYMBOLS:
            close = 1000.0
            for trade_date in dates:
                close *= 1.001
                index_rows.append({"symbol": symbol, "trade_date": trade_date, "close": close})
        self.store.upsert_index_bars(index_rows)
        self.store.save_wufu_v7_session(
            "20260727", {"context_ready": True, "daily_as_of": "20260724"}
        )
        self.runner = ETFMinutePaperRunner(
            settings=self.settings,
            store=self.store,
            client=_SourceMinuteClient(),
        )

        prior = self.store.load_paper_account("etf_minute_paper", 100_000.0, "2026-07-24")
        self.runner._buy(
            prior["account"],
            prior["positions"],
            symbol="510300.SH",
            name="沪深300ETF",
            price=3.5,
            target_weight=1.0,
            trade_date="2026-07-24",
            dedupe_key="seed:BUY:510300.SH",
            reason="seed",
            slippage_rate=0.0,
            commission_rate=0.0,
            min_commission=0.0,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_source_schedule_sells_then_buys_full_cash_without_ai_or_caps(self) -> None:
        ranking = self.runner.run_once(_at("13:08"))
        self.assertEqual(ranking["plan"]["strategy_id"], "wufu_v7_static_intraday")
        self.assertEqual(ranking["plan"]["ai_review"]["status"], "not_used_by_source_file_strategy")
        self.assertEqual(ranking["plan"]["target"]["symbol"], "513100.SH")
        self.assertEqual(ranking["orders"], [])

        sell = self.runner.run_once(_at("13:09"))
        self.assertEqual([order["side"] for order in sell["orders"]], ["SELL"])
        buy = self.runner.run_once(_at("13:10"))
        self.assertEqual([order["side"] for order in buy["orders"]], ["BUY"])
        self.assertEqual(buy["orders"][0]["symbol"], "513100.SH")
        self.assertGreater(buy["orders"][0]["quantity"] * buy["orders"][0]["price"], 90_000.0)
        self.assertAlmostEqual(buy["orders"][0]["commission"] / (buy["orders"][0]["quantity"] * buy["orders"][0]["price"]), 0.0001, places=6)

    def test_non_source_minute_is_silent(self) -> None:
        result = self.runner.run_once(_at("13:11"))
        self.assertEqual(result["status"], "silent_source_schedule_window")
        self.assertFalse(result["orders"])
        self.assertFalse(result["plan"])

    def test_source_ranking_uses_batch_snapshot_without_full_pool_direct_fanout(self) -> None:
        client = _BatchSourceClient()
        runner = ETFMinutePaperRunner(settings=self.settings, store=self.store, client=client)
        result = runner.run_once(_at("13:08"))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["plan"]["target"]["symbol"], "513100.SH")
        self.assertEqual(client.direct_calls, 0)

    def test_context_bootstrap_rebuilds_static_history_with_fallbacks(self) -> None:
        client = _BootstrapSourceClient()
        store = ETFLocalStore(Path(self.temp_dir.name) / "bootstrap.sqlite")
        store.upsert_universe([ETFInstrument(symbol=symbol, name=symbol) for symbol in FILE_ETF_SYMBOLS])
        runner = ETFMinutePaperRunner(settings=self.settings, store=store, client=client)

        result = runner.refresh_source_context(_at("09:00"))
        session = store.load_wufu_v7_session("20260727")

        self.assertEqual(result["status"], "ready")
        self.assertTrue(session["context_ready"])
        self.assertEqual(set(client.daily_history_calls), FILE_ETF_SYMBOLS | {DEFENSIVE_ETF_SYMBOL})
        self.assertEqual(set(client.index_history_calls), set(WUFU_INDEX_SYMBOLS))
        self.assertEqual(session["daily_history_coverage"]["ready_symbols"], len(FILE_ETF_SYMBOLS))


def _at(clock: str) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime(2026, 7, 27, hour, minute, 5, tzinfo=BEIJING)


def _business_dates(start: str, count: int) -> list[str]:
    current = pd.Timestamp(start)
    result: list[str] = []
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return result


def _bar(symbol: str, trade_date: str, close: float) -> ETFBar:
    return ETFBar(
        symbol=symbol,
        trade_date=trade_date,
        open=close,
        high=close,
        low=close,
        close=close,
        pre_close=close,
        pct_chg=0.0,
        volume=10_000.0,
        amount=100_000.0,
        source="test",
    )


if __name__ == "__main__":
    unittest.main()
