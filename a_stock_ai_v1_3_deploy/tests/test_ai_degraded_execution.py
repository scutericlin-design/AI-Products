from __future__ import annotations

from unittest import TestCase

from notify.feishu import _format_execution_message
from simulation.paper_trading import _ai_execution_context


class AIDegradedExecutionTests(TestCase):
    def test_execution_context_marks_paper_order_audit_fields(self) -> None:
        context = _ai_execution_context(
            {
                "ai_degraded": True,
                "ai_provider": "fallback_error",
                "ai_error": "both models timed out",
            }
        )

        self.assertTrue(context["ai_degraded"])
        self.assertEqual(context["ai_execution_mode"], "hybrid_alpha_rules_only")
        self.assertIn("deepseek_then_minimax_then_stepfun", context["ai_execution_chain"])
        self.assertEqual(context["ai_provider"], "fallback_error")

    def test_execution_message_discloses_rule_only_mode(self) -> None:
        message = _format_execution_message(
            [
                {
                    "side": "BUY",
                    "symbol": "600000.SH",
                    "name": "浦发银行",
                    "status": "filled",
                    "quantity": 100,
                    "price": 10.0,
                    "strategy_id": "hybrid_alpha",
                    "ai_degraded": True,
                }
            ]
        )

        self.assertIn("AI执行：降级模式", message)
        self.assertIn("原信号、原仓位和硬风控", message)
