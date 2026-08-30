from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from etf_strategy.config import ETFStrategySettings
from etf_strategy.tushare_client import TuShareETFClient


class ETFRealtimeFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        settings = ETFStrategySettings(
            project_root=root,
            storage_dir=root,
            db_path=root / "etf.sqlite",
            reports_dir=root / "reports",
            tushare_token="test",
            tushare_base_url=None,
            minimax_api_key=None,
            minimax_endpoint=None,
            minimax_model="test",
            ai_enabled=False,
            request_pause_seconds=0.0,
            daily_amount_multiplier=1000.0,
            min_avg_turnover_yuan=0.0,
            turnover_to_order_multiple=1.0,
            max_nav_premium_pct=0.03,
        )
        self.client = TuShareETFClient(settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_empty_tushare_minutes_fall_back_to_akshare(self) -> None:
        fallback = pd.DataFrame(
            [{"时间": "2026-07-27 10:00:00", "开盘": 1.0, "最高": 1.1, "最低": 0.99, "收盘": 1.05, "成交量": 100, "成交额": 105}]
        )
        with patch.object(self.client, "_call_first", return_value=(pd.DataFrame(), "rt_etf_min_daily")), patch.object(
            self.client,
            "_fetch_today_minutes_from_tushare_history",
            return_value=pd.DataFrame(),
        ), patch.object(
                self.client,
                "_fetch_today_minutes_akshare",
                return_value=self.client._realtime_minute_frame(fallback, "akshare:fund_etf_hist_min_em"),
        ):
            result = self.client.fetch_today_minutes("513350.SH")

        self.assertEqual(len(result), 1)
        self.assertEqual(result.attrs["source"], "akshare:fund_etf_hist_min_em")

    def test_empty_realtime_minutes_prefer_tushare_history_before_akshare(self) -> None:
        history = pd.DataFrame(
            [{"time": "2026-07-27 10:00:00", "close": 1.05, "amount": 105}]
        )
        history.attrs["source"] = "tushare:etf_mins_current_day"
        with patch.object(self.client, "_call_first", return_value=(pd.DataFrame(), "rt_etf_min_daily")), patch.object(
            self.client,
            "_fetch_today_minutes_from_tushare_history",
            return_value=history,
        ), patch.object(self.client, "_fetch_today_minutes_akshare") as akshare:
            result = self.client.fetch_today_minutes("513350.SH")

        self.assertEqual(len(result), 1)
        self.assertEqual(result.attrs["source"], "tushare:etf_mins_current_day")
        akshare.assert_not_called()

    def test_batch_spot_snapshot_covers_requested_etfs_as_minute_bars(self) -> None:
        spot = pd.DataFrame(
            [
                {"代码": "510300", "最新价": 3.95, "开盘价": 3.90, "最高价": 3.97, "最低价": 3.88, "成交量": 100, "成交额": 395},
                {"代码": "159915", "最新价": 2.11, "开盘价": 2.08, "最高价": 2.12, "最低价": 2.07, "成交量": 200, "成交额": 422},
            ]
        )
        fake_akshare = SimpleNamespace(fund_etf_spot_em=lambda: spot)
        with patch.dict("sys.modules", {"akshare": fake_akshare}):
            result = self.client.fetch_realtime_spot_minutes(
                {"510300.SH", "159915.SZ", "513100.SH"},
                datetime(2026, 7, 27, 13, 8, 45, tzinfo=ZoneInfo("Asia/Shanghai")),
            )

        self.assertEqual(set(result), {"510300.SH", "159915.SZ"})
        self.assertEqual(result["510300.SH"].iloc[0]["trade_time"].strftime("%H:%M:%S"), "13:08:00")
        self.assertEqual(result["159915.SZ"].attrs["source"], "akshare:fund_etf_spot_em")

    def test_batch_spot_can_fill_missing_prior_close_snapshot(self) -> None:
        spot = pd.DataFrame(
            [{"代码": "510300", "最新价": 3.95, "开盘价": 3.90, "最高价": 3.97, "最低价": 3.88, "昨收": 3.89, "涨跌幅": 1.54, "成交量": 100, "成交额": 395}]
        )
        fake_akshare = SimpleNamespace(fund_etf_spot_em=lambda: spot)
        with patch.dict("sys.modules", {"akshare": fake_akshare}):
            bars = self.client.fetch_realtime_spot_daily({"510300.SH"}, "20260724")

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].symbol, "510300.SH")
        self.assertEqual(bars[0].trade_date, "20260724")
        self.assertEqual(bars[0].source, "akshare:fund_etf_spot_em_prior_close")

    def test_akshare_daily_history_preserves_ohlcv_for_one_static_etf(self) -> None:
        history = pd.DataFrame(
            [
                {
                    "日期": "2026-07-24",
                    "开盘": 3.90,
                    "收盘": 3.95,
                    "最高": 3.97,
                    "最低": 3.88,
                    "成交量": 100,
                    "成交额": 395,
                    "涨跌额": 0.05,
                    "涨跌幅": 1.28,
                }
            ]
        )
        fake_akshare = SimpleNamespace(fund_etf_hist_em=lambda **kwargs: history)
        with patch.dict("sys.modules", {"akshare": fake_akshare}):
            bars = self.client.fetch_daily_history_akshare("510300.SH", "20260701", "20260724")

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].symbol, "510300.SH")
        self.assertEqual(bars[0].trade_date, "20260724")
        self.assertAlmostEqual(bars[0].pre_close, 3.90)
        self.assertEqual(bars[0].source, "akshare:fund_etf_hist_em")

    def test_daily_history_prefers_symbol_scoped_tushare_before_akshare(self) -> None:
        history = pd.DataFrame(
            [
                {
                    "ts_code": "510300.SH",
                    "trade_date": "20260724",
                    "pre_close": 3.90,
                    "open": 3.90,
                    "high": 3.97,
                    "low": 3.88,
                    "close": 3.95,
                    "pct_chg": 1.28,
                    "vol": 100,
                    "amount": 395,
                }
            ]
        )
        with patch.object(self.client, "_call_first", return_value=(history, "fund_daily")) as tushare, patch.object(
            self.client, "fetch_daily_history_akshare"
        ) as akshare:
            bars = self.client.fetch_daily_history("510300.SH", "20260701", "20260724")

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].source, "tushare:fund_daily")
        tushare.assert_called_once()
        akshare.assert_not_called()

    def test_akshare_index_history_normalizes_exchange_and_date_range(self) -> None:
        history = pd.DataFrame(
            [
                {"date": "2026-07-23", "open": 4000, "high": 4010, "low": 3990, "close": 4005, "volume": 1},
                {"date": "2026-07-24", "open": 4010, "high": 4020, "low": 4000, "close": 4015, "volume": 2},
                {"date": "2026-07-27", "open": 4020, "high": 4030, "low": 4010, "close": 4025, "volume": 3},
            ]
        )
        fake_akshare = SimpleNamespace(stock_zh_index_daily=lambda **kwargs: history)
        with patch.dict("sys.modules", {"akshare": fake_akshare}):
            rows = self.client.fetch_index_daily_akshare("000300.SH", "20260724", "20260724")

        self.assertEqual(rows, [
            {
                "symbol": "000300.SH",
                "trade_date": "20260724",
                "open": 4010.0,
                "high": 4020.0,
                "low": 4000.0,
                "close": 4015.0,
                "pre_close": 0.0,
                "pct_chg": 0.0,
                "amount": 0.0,
            }
        ])


if __name__ == "__main__":
    unittest.main()
