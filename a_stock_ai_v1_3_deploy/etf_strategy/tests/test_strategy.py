from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from etf_strategy.config import ETFStrategySettings
from etf_strategy.strategy import INDEX_SYMBOLS, WufuETFStrategy


class WufuETFStrategyTests(unittest.TestCase):
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
            min_avg_turnover_yuan=20_000_000.0,
            turnover_to_order_multiple=20.0,
            max_nav_premium_pct=0.03,
        )
        self.strategy = WufuETFStrategy(self.settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_selects_highest_quality_momentum_etf(self) -> None:
        dates = _business_dates(45)
        universe = pd.DataFrame(
            [
                {"symbol": "510300.SH", "name": "沪深300ETF", "index_code": "000300.SH", "index_name": "沪深300", "etf_type": "境内"},
                {"symbol": "513100.SH", "name": "纳指ETF", "index_code": "NDX", "index_name": "纳斯达克", "etf_type": "QDII"},
            ]
        )
        daily = pd.concat(
            [_bars("510300.SH", dates, 0.002), _bars("513100.SH", dates, 0.004)],
            ignore_index=True,
        )
        index_daily = _index_bars(dates, 0.001)

        plan = self.strategy.decide(
            as_of=dates[-1],
            universe=universe,
            daily=daily,
            index_daily=index_daily,
            account_value=1_000_000.0,
        )

        self.assertEqual(plan.regime, "normal")
        self.assertEqual(plan.signal, "BUY")
        self.assertEqual(plan.target["symbol"], "513100.SH")
        self.assertAlmostEqual(plan.target_weight, 0.70)

    def test_missing_minute_confirmation_vetoes_buy(self) -> None:
        plan = self.strategy.decide(
            as_of="20260130",
            universe=pd.DataFrame(),
            daily=pd.DataFrame(),
            index_daily=pd.DataFrame(),
            account_value=1_000_000.0,
        )
        plan.signal = "BUY"
        plan.target_weight = 0.7
        plan.target = {"symbol": "510300.SH", "name": "沪深300ETF", "latest_price": 4.0}
        plan.trade_plan = [{"action": "BUY", "symbol": "510300.SH"}]

        plan = self.strategy.confirm_intraday(plan, pd.DataFrame())

        self.assertEqual(plan.signal, "HOLD")
        self.assertEqual(plan.target_weight, 0.0)
        self.assertEqual(plan.trade_plan, [])
        self.assertEqual(plan.intraday_confirmation["status"], "unavailable")


def _business_dates(count: int) -> list[str]:
    result: list[str] = []
    current = date(2026, 1, 1)
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return result


def _bars(symbol: str, dates: list[str], daily_return: float) -> pd.DataFrame:
    rows = []
    close = 1.0
    for trade_date in dates:
        pre_close = close
        close *= 1 + daily_return
        rows.append(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "open": pre_close,
                "high": close,
                "low": pre_close,
                "close": close,
                "pre_close": pre_close,
                "pct_chg": daily_return * 100,
                "volume": 1_000_000,
                "amount": 50_000,
            }
        )
    return pd.DataFrame(rows)


def _index_bars(dates: list[str], daily_return: float) -> pd.DataFrame:
    return pd.concat([_bars(symbol, dates, daily_return) for symbol in INDEX_SYMBOLS], ignore_index=True)


if __name__ == "__main__":
    unittest.main()
