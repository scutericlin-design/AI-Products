from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from etf_strategy.config import ETFStrategySettings
from etf_strategy.models import ETFBar, ETFInstrument
from etf_strategy.runner import ETFStrategyRunner
from etf_strategy.storage import ETFLocalStore
from etf_strategy.strategy import INDEX_SYMBOLS, WufuETFStrategy


class ETFStrategyRunnerTests(unittest.TestCase):
    def test_decision_and_backtest_stay_local(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = _settings(root)
            store = ETFLocalStore(settings.db_path)
            dates = _business_dates(45)
            store.upsert_universe(
                [
                    ETFInstrument(
                        symbol="510300.SH",
                        name="沪深300ETF",
                        index_code="000300.SH",
                        index_name="沪深300",
                        exchange="SSE",
                        etf_type="境内",
                        list_date="20200101",
                    ),
                    ETFInstrument(
                        symbol="513100.SH",
                        name="纳指ETF",
                        index_code="NDX",
                        index_name="纳斯达克",
                        exchange="SSE",
                        etf_type="QDII",
                        list_date="20200101",
                    ),
                ]
            )
            store.upsert_daily_bars(_bars("510300.SH", dates, 0.002))
            store.upsert_daily_bars(_bars("513100.SH", dates, 0.004))
            for symbol in INDEX_SYMBOLS:
                store.upsert_index_bars(
                    [
                        {
                            "symbol": symbol,
                            "trade_date": item.trade_date,
                            "open": item.open,
                            "high": item.high,
                            "low": item.low,
                            "close": item.close,
                            "pre_close": item.pre_close,
                            "pct_chg": item.pct_chg,
                            "amount": item.amount,
                        }
                        for item in _bars(symbol, dates, 0.001)
                    ]
                )

            runner = ETFStrategyRunner(settings=settings, store=store, strategy=WufuETFStrategy(settings))
            decision = runner.decide(as_of=dates[-1])
            backtest = runner.backtest(start_date=dates[30], end_date=dates[-1])

            self.assertEqual(decision["signal"], "BUY")
            self.assertEqual(decision["target"]["symbol"], "513100.SH")
            self.assertTrue(Path(decision["report_path"]).is_file())
            self.assertGreaterEqual(backtest["trade_count"], 1)
            self.assertTrue(Path(backtest["report_path"]).is_file())
            self.assertTrue(settings.db_path.is_file())


def _settings(root: Path) -> ETFStrategySettings:
    return ETFStrategySettings(
        project_root=root,
        storage_dir=root / "local",
        db_path=root / "local" / "etf.sqlite",
        reports_dir=root / "local" / "reports",
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


def _business_dates(count: int) -> list[str]:
    result: list[str] = []
    current = date(2026, 1, 1)
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return result


def _bars(symbol: str, dates: list[str], daily_return: float) -> list[ETFBar]:
    rows: list[ETFBar] = []
    close = 1.0
    for trade_date in dates:
        pre_close = close
        close *= 1 + daily_return
        rows.append(
            ETFBar(
                symbol=symbol,
                trade_date=trade_date,
                open=pre_close,
                high=close,
                low=pre_close,
                close=close,
                pre_close=pre_close,
                pct_chg=daily_return * 100,
                volume=1_000_000,
                amount=50_000,
                source="test",
            )
        )
    return rows


if __name__ == "__main__":
    unittest.main()
