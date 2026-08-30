from __future__ import annotations

from unittest import TestCase

from notify.feishu import _format_market_status_message


class MarketStatusMessageTests(TestCase):
    def test_status_message_distinguishes_rule_candidates_from_execution(self) -> None:
        message = _format_market_status_message(
            {
                "state": {"state": "UPTREND", "phase": "morning", "volume": "high"},
                "market_sentiment": {"sentiment_score": 66, "sentiment_status": "warm", "trade_permission": "BUY_ALLOWED"},
                "leader": {"stock": "600000.SH", "name": "浦发银行", "strength": 86, "pct_change": 3.2},
                "recommendation_bundle": {"candidate_count": 88, "recommendation_count": 2},
                "final_signal": {"signal": "BUY", "position": 0.06, "recommendation_count": 2},
                "paper_simulation": {"summary": {"filled_orders": 1}},
                "ai_result": {"ai_error": "timeout", "ai_degraded": True},
            }
        )

        self.assertIn("规则可买 2 只", message)
        self.assertIn("当前执行 2 只", message)
        self.assertIn("规则引擎降级执行", message)
        self.assertIn("仓位维持规则计算结果", message)
