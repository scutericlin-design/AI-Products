from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from etf_strategy.config import ETFStrategySettings
from etf_strategy.storage import ETFLocalStore
from etf_strategy.wufu_v7_static import WUFU_INDEX_SYMBOLS, WufuV7StaticMinuteBacktester


class WufuV7StaticTests(unittest.TestCase):
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
            ai_enabled=False,
            request_pause_seconds=0.0,
            daily_amount_multiplier=1000.0,
            min_avg_turnover_yuan=0.0,
            turnover_to_order_multiple=1.0,
            max_nav_premium_pct=0.03,
        )
        self.engine = WufuV7StaticMinuteBacktester(self.settings, ETFLocalStore(self.settings.db_path))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_source_ranking_selects_highest_intraday_momentum(self) -> None:
        dates = _business_dates(32)
        daily = {
            "510300.SH": _daily("510300.SH", dates[:-1], 0.001),
            "513100.SH": _daily("513100.SH", dates[:-1], 0.003),
        }
        index_daily = pd.concat([_daily(symbol, dates[:-1], 0.001) for symbol in WUFU_INDEX_SYMBOLS], ignore_index=True)
        minute_by_symbol = {
            "510300.SH": _minutes(dates[-1], 1.04, count=28),
            "513100.SH": _minutes(dates[-1], 1.10, count=28),
        }

        target, details = self.engine._select_target(
            dates[-1], None, minute_by_symbol, daily, index_daily, {"510300.SH": "A", "513100.SH": "B"}
        )

        self.assertEqual(target, "513100.SH")
        self.assertFalse(details["weak_market"])

    def test_minute_trend_requires_positive_normalized_slope(self) -> None:
        rising = _minutes("20260216", 1.0, step=0.0002, count=30)
        falling = _minutes("20260216", 1.1, step=-0.0002, count=30)

        self.assertTrue(self.engine._is_uptrend(rising, "13:10:00"))
        self.assertFalse(self.engine._is_uptrend(falling, "13:10:00"))


def _business_dates(count: int) -> list[str]:
    current = date(2026, 1, 1)
    output: list[str] = []
    while len(output) < count:
        if current.weekday() < 5:
            output.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return output


def _daily(symbol: str, dates: list[str], return_rate: float) -> pd.DataFrame:
    close = 1.0
    rows = []
    for trade_date in dates:
        close *= 1 + return_rate
        rows.append({"symbol": symbol, "trade_date": trade_date, "close": close, "amount": 100_000.0})
    return pd.DataFrame(rows)


def _minutes(trade_date: str, start: float, step: float = 0.0, count: int = 1) -> pd.DataFrame:
    base = pd.Timestamp(f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]} 12:41:00")
    return pd.DataFrame(
        {
            "trade_time": [base + pd.Timedelta(minutes=index) for index in range(count)],
            "close": [start + step * index for index in range(count)],
        }
    )


if __name__ == "__main__":
    unittest.main()
