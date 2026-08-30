from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard.data import build_etf_dashboard_payload
from etf_strategy.storage import ETFLocalStore


class ETFDashboardPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "etf.sqlite"
        self.store = ETFLocalStore(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_payload_exposes_only_etf_paper_state_and_latest_plan(self) -> None:
        account_state = self.store.load_paper_account("etf_minute_paper", 100_000.0, "2026-07-28")
        account = account_state["account"]
        account["cash"] = 70_000.0
        positions = {
            "510300.SH": {
                "name": "沪深300ETF",
                "quantity": 8_000,
                "available_quantity": 8_000,
                "avg_cost": 3.70,
                "last_price": 3.75,
                "last_trade_date": "2026-07-27",
            }
        }
        self.store.save_paper_account_state(
            account,
            positions,
            order={
                "order_id": "order-1",
                "dedupe_key": "20260728:BUY:510300.SH",
                "side": "BUY",
                "symbol": "510300.SH",
                "name": "沪深300ETF",
                "status": "filled",
                "quantity": 8_000,
                "price": 3.75,
                "commission": 5.0,
                "reason": "test",
                "created_at": "2026-07-28T10:00:00+08:00",
            },
        )
        self.store.save_decision(
            {
                "strategy_id": "wufu_etf_v7",
                "strategy_version": "v7",
                "as_of": "2026-07-28",
                "regime": "normal",
                "regime_detail": {"index_name": "沪深300"},
                "signal": "BUY",
                "target_weight": 0.70,
                "target": {"symbol": "510300.SH", "name": "沪深300ETF", "latest_price": 3.75},
                "candidates": [{"symbol": "510300.SH", "name": "沪深300ETF", "momentum_score": 1.2}],
                "minute_execution": {"status": "ok", "filled_order_count": 1},
            }
        )

        with patch("dashboard.data._etf_db_path", return_value=self.db_path):
            payload = build_etf_dashboard_payload()

        self.assertTrue(payload["read_only"])
        self.assertTrue(payload["no_real_orders"])
        self.assertEqual(payload["latest_decision"]["signal"], "BUY")
        self.assertEqual(payload["latest_decision"]["target"]["symbol"], "510300.SH")
        self.assertEqual(payload["account"]["equity"], 100_000.0)
        self.assertEqual(payload["positions"][0]["unrealized_pnl"], 400.0)
        self.assertEqual(payload["recent_orders"][0]["side"], "BUY")


if __name__ == "__main__":
    unittest.main()
