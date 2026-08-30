from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from etf_strategy.config import ETFStrategySettings
from etf_strategy.live_runner import ETFMinutePaperRunner, _normalize_realtime_minutes
from etf_strategy.models import ETFDecisionPlan
from etf_strategy.storage import ETFLocalStore
from notify.feishu import _format_etf_execution_message, _format_etf_recommendation_message


class ETFMinutePaperRunnerTests(unittest.TestCase):
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
            minute_initial_cash=100_000.0,
            minute_max_position_weight=0.70,
            minute_slippage_rate=0.0,
            minute_commission_rate=0.0,
            minute_min_commission=0.0,
        )
        self.store = ETFLocalStore(self.settings.db_path)
        self.runner = ETFMinutePaperRunner(settings=self.settings, store=self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_buy_is_deduplicated_and_sell_respects_t1(self) -> None:
        state = self.store.load_paper_account("etf_minute_paper", 100_000.0, "2026-07-27")
        account = state["account"]
        positions = state["positions"]
        buy = self.runner._buy(
            account,
            positions,
            symbol="510300.SH",
            name="沪深300ETF",
            price=4.0,
            target_weight=0.70,
            trade_date="2026-07-27",
            dedupe_key="20260727:BUY:510300.SH",
            reason="test",
            execution_context={
                "ai_degraded": True,
                "ai_execution_mode": "etf_strategy_rules_only",
                "ai_execution_chain": "stepfun_then_minimax_then_etf_strategy_rules",
                "ai_provider": "fallback_error",
                "ai_error": "test failure",
            },
        )
        self.assertIsNotNone(buy)
        self.assertGreater(int(buy["quantity"]), 0)
        self.assertTrue(buy["ai_degraded"])
        self.assertEqual(positions["510300.SH"]["available_quantity"], 0)
        with self.store._connect() as db:
            stored = db.execute(
                "SELECT execution_json FROM etf_paper_orders WHERE dedupe_key = ?",
                ("20260727:BUY:510300.SH",),
            ).fetchone()
        self.assertIn('"ai_degraded": true', stored["execution_json"])
        self.assertIsNone(
            self.runner._sell(
                account,
                positions,
                symbol="510300.SH",
                price=4.1,
                trade_date="2026-07-27",
                dedupe_key="20260727:SELL:510300.SH",
                reason="test",
            )
        )

        rolled = self.store.load_paper_account("etf_minute_paper", 100_000.0, "2026-07-28")
        sell = self.runner._sell(
            rolled["account"],
            rolled["positions"],
            symbol="510300.SH",
            price=4.1,
            trade_date="2026-07-28",
            dedupe_key="20260728:SELL:510300.SH",
            reason="test",
        )
        self.assertIsNotNone(sell)
        self.assertEqual(sell["side"], "SELL")
        self.assertFalse(rolled["positions"])

    def test_realtime_time_only_rows_use_beijing_trade_date(self) -> None:
        now = datetime(2026, 7, 27, 10, 0, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
        bars = _normalize_realtime_minutes(pd.DataFrame([{"time": "09:59:00", "close": 3.21}]), now)
        self.assertEqual(str(bars.iloc[0]["trade_time"]), "2026-07-27 09:59:00")

    def test_etf_push_contains_only_trade_fields(self) -> None:
        message = _format_etf_execution_message(
            [{"side": "BUY", "symbol": "510300.SH", "name": "沪深300ETF", "price": 3.21, "quantity": 1200}]
        )
        self.assertIn("买入ETF：510300.SH 沪深300ETF", message)
        self.assertIn("成交价：3.21", message)
        self.assertIn("成交份额：1200 份", message)
        self.assertNotIn("仓位", message)

        degraded_message = _format_etf_execution_message(
            [
                {
                    "side": "BUY",
                    "symbol": "510300.SH",
                    "name": "沪深300ETF",
                    "price": 3.21,
                    "quantity": 1200,
                    "ai_degraded": True,
                }
            ]
        )
        self.assertIn("AI执行：降级模式", degraded_message)

    def test_etf_recommendation_push_marks_target_as_not_filled(self) -> None:
        message = _format_etf_recommendation_message(
            {
                "regime": "normal",
                "target": {"symbol": "513100.SH", "name": "纳指ETF", "price": 1.23},
                "candidates": [
                    {"symbol": "513100.SH", "momentum_score": 1.2345, "r_squared": 0.6789}
                ],
            }
        )
        self.assertIn("目标ETF：513100.SH 纳指ETF", message)
        self.assertIn("13:08快照价：1.23", message)
        self.assertIn("不代表已成交", message)

    def test_minute_hold_is_saved_for_audit(self) -> None:
        state = self.store.load_paper_account("etf_minute_paper", 100_000.0, "2026-07-27")
        plan = ETFDecisionPlan(
            strategy_id="test",
            strategy_version="test",
            as_of="2026-07-24",
            regime="neutral",
            regime_detail={},
            signal="HOLD",
            target_weight=0.0,
            target=None,
            current_symbol=None,
            trade_plan=[],
            candidates=[],
            reasoning="无合格ETF候选，保持现金。",
        )

        result = self.runner._complete("ok", state["account"], state["positions"], [], "", plan, [])

        self.assertTrue(result["decision_id"])
        with self.store._connect() as db:
            row = db.execute(
                "SELECT signal, payload_json FROM etf_decisions WHERE decision_id = ?",
                (result["decision_id"],),
            ).fetchone()
        self.assertEqual(row["signal"], "HOLD")
        self.assertIn('"filled_order_count": 0', row["payload_json"])


if __name__ == "__main__":
    unittest.main()
