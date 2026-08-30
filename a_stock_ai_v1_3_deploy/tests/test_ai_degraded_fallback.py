from __future__ import annotations

from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from ai.minimax_client import _ai_unavailable_signal
from app.config import settings


class AIDegradedFallbackTests(TestCase):
    def test_buy_preserves_rule_signal_and_position_when_ai_is_unavailable(self) -> None:
        fallback = {
            "signal": "BUY",
            "position": 0.12,
            "risk_level": "medium",
            "reasoning": "规则信号已通过筛选",
            "recommendations": [{"symbol": "600000.SH", "can_buy": True}],
            "risk_flags": ["base_filter"],
        }
        with patch(
            "ai.minimax_client.settings",
            replace(settings, ai_degraded_fallback_enabled=True, ai_degraded_position_multiplier=0.5),
        ):
            result = _ai_unavailable_signal(fallback, "prompt", "custom", "timeout")

        self.assertEqual(result["signal"], "BUY")
        self.assertEqual(result["position"], 0.12)
        self.assertEqual(result["risk_level"], "medium")
        self.assertTrue(result["ai_degraded"])
        self.assertEqual(result["ai_execution_mode"], "hybrid_alpha_rules_only")
        self.assertIn("ai_degraded_rule_execution", result["risk_flags"])
        self.assertIn("原仓位", result["reasoning"])

    def test_hold_is_also_auditable_rule_only_decision(self) -> None:
        result = _ai_unavailable_signal({"signal": "HOLD", "position": 0.0}, "prompt", "custom", "timeout")

        self.assertEqual(result["signal"], "HOLD")
        self.assertEqual(result["position"], 0.0)
        self.assertTrue(result["ai_degraded"])
        self.assertEqual(result["ai_execution_mode"], "hybrid_alpha_rules_only")
