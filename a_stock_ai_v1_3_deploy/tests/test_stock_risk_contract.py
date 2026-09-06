from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from ai.minimax_client import _normalize_minimax_payload
from app.config import settings
from engine.ranking_engine import merge_signal_with_recommendations
from engine.risk_engine import risk_check


class StockRiskContractTests(TestCase):
    def setUp(self) -> None:
        tuned = replace(settings, max_position_weight=0.12, multi_strategy_max_exposure=0.85)
        for module in ("engine.risk_engine", "engine.position_engine"):
            self.enterContext(patch(f"{module}.settings", tuned))
        self.enterContext(patch("engine.risk_engine.param_float", side_effect=lambda name, default: default))

    def signal(self, weights: tuple[float, ...] = (0.08, 0.08, 0.08)) -> dict:
        bundle = {
            "signal": "BUY",
            "position": round(min(sum(weights), 0.12), 4),
            "risk_level": "normal",
            "market_sentiment": {"trade_permission": "BUY_ALLOWED", "panic_score": 0},
            "recommendations": [
                {
                    "symbol": f"{600000 + index}.SH", "position": weight,
                    "target_weight": weight, "strategy_weights": {"stock": weight},
                    "can_buy": True, "current_price": 10.0,
                }
                for index, weight in enumerate(weights)
            ],
        }
        return merge_signal_with_recommendations(
            {"signal": "HOLD", "position": 0, "state": {"state": "UPTREND"}, "reasoning": "rules"},
            bundle,
        )

    def ai_review(self, signal: dict, budget: float) -> dict:
        payload = {"choices": [{"message": {"content": json.dumps({"signal": "BUY", "position": budget})}}]}
        return _normalize_minimax_payload(payload, signal, "fixture", "fixture", [])

    def test_free_text_never_overrides_structured_state(self) -> None:
        for field in ("reasoning", "prompt", "raw", "leader", "watchlist"):
            with self.subTest(field=field):
                signal = self.signal()
                signal[field] = [{"state": "DOWNTREND", "text": "No downtrend is present"}]
                checked = risk_check(signal)
                self.assertEqual(checked["signal"], "BUY")
                self.assertNotIn("forced_downtrend_sell", checked.get("risk_flags", []))

    def test_structured_downtrend_still_forces_zero_budget_sell(self) -> None:
        signal = self.signal()
        signal["state"] = {"state": " downtrend "}
        checked = risk_check(signal)
        self.assertEqual(checked["signal"], "SELL")
        self.assertEqual(checked["position"], 0)
        self.assertEqual(checked["recommendations"], [])
        self.assertIn("forced_downtrend_sell", checked["risk_flags"])

    def test_bundle_cap_does_not_collapse_candidate_weights(self) -> None:
        checked = risk_check(self.signal())
        self.assertEqual(checked["position"], 0.12)
        self.assertAlmostEqual(sum(item["position"] for item in checked["recommendations"]), 0.24)
        self.assertEqual(checked["risk_budget"]["candidate_weight_scale"], 1.0)

    def test_upstream_ai_scalar_reduction_scales_all_executable_weights(self) -> None:
        signal = self.signal()
        original = deepcopy(signal)
        reviewed = self.ai_review(signal, 0.06)
        self.assertEqual([item["position"] for item in reviewed["recommendations"]], [0.08] * 3)
        checked = risk_check(reviewed)
        self.assertEqual(checked["risk_budget"]["before_ai"], 0.12)
        for item in checked["recommendations"]:
            self.assertEqual(item["position"], 0.04)
            self.assertEqual(item["target_weight"], 0.04)
            self.assertEqual(item["strategy_weights"], {"stock": 0.04})
        self.assertEqual(signal, original)

    def test_below_cap_budget_uses_actual_bundle_denominator(self) -> None:
        checked = risk_check(self.ai_review(self.signal((0.02, 0.02)), 0.01))
        self.assertEqual(checked["risk_budget"]["before_ai"], 0.04)
        self.assertEqual([item["position"] for item in checked["recommendations"]], [0.005, 0.005])

    def test_light_only_and_ai_reductions_compose_without_double_scaling(self) -> None:
        for ai_budget in (0.12, 0.08, 0.03):
            with self.subTest(ai_budget=ai_budget):
                signal = self.ai_review(self.signal(), ai_budget)
                signal["market_sentiment"]["trade_permission"] = "LIGHT_ONLY"
                checked = risk_check(signal)
                expected_budget = min(ai_budget, 0.06)
                self.assertEqual(checked["position"], expected_budget)
                self.assertAlmostEqual(checked["risk_budget"]["candidate_weight_scale"], expected_budget / 0.12)
                self.assertAlmostEqual(sum(item["position"] for item in checked["recommendations"]), 0.24 * expected_budget / 0.12)

    def test_already_light_sized_bundle_is_not_reduced_again(self) -> None:
        signal = self.signal((0.02, 0.02))
        signal["market_sentiment"]["trade_permission"] = "LIGHT_ONLY"
        checked = risk_check(signal)
        self.assertEqual([item["position"] for item in checked["recommendations"]], [0.02, 0.02])

    def test_ai_cannot_enlarge_candidates_and_degraded_rules_keep_weights(self) -> None:
        checked = risk_check(self.ai_review(self.signal(), 0.8))
        self.assertEqual([item["position"] for item in checked["recommendations"]], [0.08] * 3)
        signal = self.signal()
        signal["ai_degraded"] = True
        self.assertEqual(risk_check(signal)["recommendations"], checked["recommendations"])

    def test_invalid_or_zero_budget_never_falls_back_to_candidate_weights(self) -> None:
        for budget in (0, -0.1, float("nan"), float("inf"), -float("inf"), None, "bad"):
            with self.subTest(budget=budget):
                signal = self.signal()
                signal["position"] = budget
                checked = risk_check(signal)
                self.assertEqual(checked["signal"], "HOLD")
                self.assertEqual(checked["position"], 0)
                self.assertEqual(checked["recommendations"], [])

    def test_invalid_candidate_weight_does_not_use_positive_alias(self) -> None:
        for weight in (0, -0.1, float("nan"), float("inf"), None, "bad"):
            with self.subTest(weight=weight):
                signal = self.signal()
                signal["recommendations"][0]["position"] = weight
                checked = risk_check(signal)
                self.assertEqual([item["position"] for item in checked["recommendations"]], [0.08, 0.08])

    def test_filtered_weights_are_not_redistributed_and_recheck_is_idempotent(self) -> None:
        signal = self.ai_review(self.signal(), 0.06)
        signal["recommendations"][0]["can_buy"] = False
        checked = risk_check(signal)
        self.assertEqual([item["position"] for item in checked["recommendations"]], [0.04, 0.04])
        self.assertEqual(risk_check(checked)["recommendations"], checked["recommendations"])

    def test_multi_strategy_merge_retains_its_distinct_aggregate_budget(self) -> None:
        signal = self.signal()
        signal.update(is_multi_strategy=True, position=0.24)
        checked = risk_check(self.ai_review(signal, 0.12))
        self.assertEqual(checked["risk_budget"]["before_ai"], 0.24)
        self.assertEqual([item["position"] for item in checked["recommendations"]], [0.04] * 3)

    def test_no_buy_and_panic_continue_to_block_recommendations(self) -> None:
        for sentiment in ({"trade_permission": "NO_BUY"}, {"panic_score": 100}):
            with self.subTest(sentiment=sentiment):
                signal = self.signal()
                signal["market_sentiment"] = sentiment
                checked = risk_check(signal)
                self.assertIn(checked["signal"], {"HOLD", "SELL"})
                self.assertEqual(checked["recommendations"], [])
