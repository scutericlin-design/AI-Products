from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import settings
from data.stock_universe import StockUniverseSnapshot
from etf_strategy.config import load_settings as load_etf_settings
from etf_strategy.maintenance import minute_storage_report
from etf_strategy.storage import ETFLocalStore
from portfolio.account import PaperPosition, new_account, simulate_buy, simulate_sell_all
from realtime.market_stream import MarketStream


class PaperSimulationIntegrityTests(unittest.TestCase):
    def test_partial_target_below_one_lot_is_skipped_not_rejected(self) -> None:
        account = new_account()
        account.positions["688981.SH"] = PaperPosition(
            symbol="688981.SH",
            name="中芯国际",
            quantity=100,
            available_quantity=100,
            avg_cost=127.0,
            last_price=127.0,
        )

        order, trade = simulate_buy(
            account,
            {
                "symbol": "688981.SH",
                "name": "中芯国际",
                "current_price": 127.0,
                "position": 0.02,
            },
            cycle_id="test-cycle",
        )

        self.assertIsNone(trade)
        self.assertEqual(order["status"], "skipped")
        self.assertEqual(order["skip_kind"], "target_within_lot")
        self.assertIn("不足一手", order["reason"])

    def test_t1_sell_block_is_skipped_not_rejected(self) -> None:
        account = new_account()
        account.positions["600000.SH"] = PaperPosition(
            symbol="600000.SH",
            name="浦发银行",
            quantity=100,
            available_quantity=0,
            avg_cost=10.0,
            last_price=10.0,
            last_trade_date=datetime.now().date().isoformat(),
        )

        orders, trades = simulate_sell_all(account, cycle_id="test-cycle", prices={"600000.SH": 9.8})

        self.assertFalse(trades)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["status"], "skipped")
        self.assertEqual(orders[0]["skip_kind"], "t1_locked")

    def test_stock_selection_audit_reports_the_actual_stock_universe(self) -> None:
        symbols = [f"{index:06d}.SZ" for index in range(160)]
        snapshot = StockUniverseSnapshot(
            symbols=symbols,
            tags={symbol: ["core"] for symbol in symbols},
            profile="adaptive",
            limit=160,
            as_of_date="20260807",
            source="tushare_daily_basic",
            cache_state="hit",
            updated_at="2026-08-08T09:30:00+08:00",
        )

        class FixedManager:
            def resolve(self) -> StockUniverseSnapshot:
                return snapshot

        audit = MarketStream(universe_manager=FixedManager()).selection_universe(received_count=160)

        self.assertEqual(audit["asset_class"], "stock")
        self.assertEqual(audit["received_count"], 160)
        self.assertEqual(audit["requested_count"], 160)
        self.assertTrue(audit["comparable_to_adaptive_160_backtest"])

    def test_stock_and_etf_runtime_state_are_physically_separate(self) -> None:
        etf_settings = load_etf_settings()

        self.assertNotEqual(settings.db_path.resolve(), etf_settings.db_path.resolve())
        self.assertNotEqual(settings.paper_account_path.resolve(), etf_settings.db_path.resolve())
        self.assertNotEqual(settings.storage_dir.resolve(), etf_settings.storage_dir.resolve())

    def test_etf_storage_report_is_read_only_and_uses_its_own_database(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "etf.sqlite"
            ETFLocalStore(db_path)

            report = minute_storage_report(db_path)

        self.assertTrue(report["exists"])
        self.assertEqual(Path(report["db_path"]), db_path.resolve())
        self.assertIn("保留历史分钟数据", report["retention_policy"])


if __name__ == "__main__":
    unittest.main()
