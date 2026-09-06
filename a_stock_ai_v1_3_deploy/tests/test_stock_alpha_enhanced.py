import copy
from datetime import datetime
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

from stock_alpha.enhanced import enhanced_plan, apply_opportunities
from stock_alpha.event_ai import review
from stock_alpha.ai_config import AISettings
from stock_alpha.model import ModelConfig, build_targets
from stock_alpha.paper import TZ
from tests.test_stock_alpha import sample


class EnhancedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars, financials, memberships, calendar = sample()
        cls.base = build_targets(cls.bars, financials, memberships, calendar[140], {}, ModelConfig())
        cls.plan = enhanced_plan(cls.base, {}, {}, cls.bars)

    def test_growth_has_an_effect_and_baseline_is_unchanged(self):
        baseline = copy.deepcopy(self.base)
        changed = copy.deepcopy(self.base)
        symbol = max(changed["scores"], key=lambda row: row["or_yoy"])["ts_code"]
        for row in changed["scores"]:
            if row["ts_code"] == symbol:
                row["or_yoy"] = -10000
        output = enhanced_plan(changed, {}, {}, self.bars)
        before = next(row["score"] for row in self.plan["scores"] if row["ts_code"] == symbol)
        after = next(row["score"] for row in output["scores"] if row["ts_code"] == symbol)
        self.assertLess(after, before)
        self.assertEqual(self.base, baseline)

    def test_future_prices_do_not_change_enhanced_decisions(self):
        changed = self.bars.copy()
        changed.loc[changed.trade_date > self.base["as_of"], "close"] = 1e9
        self.assertEqual(self.plan, enhanced_plan(self.base, {}, {}, changed))

    def test_malformed_ai_symbol_preserves_rule_plan(self):
        result = apply_opportunities(self.plan, [{"ts_code": [], "event_ids": ["x"], "thesis": "x"}], [], today="20260907")
        self.assertEqual(result["weights"], self.plan["weights"])

    def event(self, symbol, **kwargs):
        return {"id": symbol, "ts_code": symbol, "positive_numeric_evidence": True,
                "ann_date": "20260901", "expires": "20261001", **kwargs}

    def test_ai_never_exceeds_caps_or_uses_outside_universe(self):
        symbols = [row["ts_code"] for row in self.plan["scores"] if row["ts_code"] not in self.plan["weights"]][:3]
        events = [self.event(symbol) for symbol in symbols]
        ideas = [{"ts_code": symbol, "event_ids": [symbol], "thesis": "Evidence-backed growth"} for symbol in symbols]
        result = apply_opportunities(self.plan, ideas, events, today="20260907")
        self.assertLessEqual(sum(result["weights"].values()), 0.80000001)
        self.assertLessEqual(max(result["weights"].values()), 0.08000001)
        self.assertLessEqual(len(result["weights"]), 12)
        self.assertLessEqual(result["ai_positive_active_weight"], 0.20000001)
        self.assertGreater(result["ai_positive_active_weight"], 0)
        denied = apply_opportunities(self.plan, [{"ts_code": "not-a-stock", "event_ids": ["x"], "thesis": "x"}], events, today="20260907")
        self.assertEqual(denied["weights"], self.plan["weights"])

    def test_missing_expired_future_and_cross_symbol_evidence_do_not_change_weights(self):
        symbol = self.plan["scores"][0]["ts_code"]
        idea = {"ts_code": symbol, "event_ids": [symbol], "thesis": "x"}
        for event in (self.event(symbol, positive_numeric_evidence=False), self.event(symbol, expires="20260906"),
                      self.event(symbol, ann_date="20260908"), self.event("other", id=symbol)):
            result = apply_opportunities(self.plan, [idea], [event], today="20260907")
            self.assertEqual(result["weights"], self.plan["weights"])

    @patch("stock_alpha.event_ai.requests.post")
    @patch("stock_alpha.event_ai.load_ai_settings", return_value=AISettings(api_key="test-private"))
    def test_no_evidence_does_not_call_ai(self, settings, post):
        with tempfile.TemporaryDirectory() as directory:
            result = review(self.plan, [], Path(directory), {}, datetime(2026, 9, 7, tzinfo=TZ))
        post.assert_not_called()
        self.assertEqual(result["mode"], "no_eligible_event")

    @patch("stock_alpha.event_ai.requests.post")
    @patch("stock_alpha.event_ai.load_ai_settings", return_value=AISettings(api_key="test-private"))
    def test_failure_falls_back_to_own_rules_without_secret_in_report(self, settings, post):
        import requests
        post.side_effect = requests.Timeout("test-private")
        event = self.event(self.plan["scores"][0]["ts_code"])
        with tempfile.TemporaryDirectory() as directory:
            result = review(self.plan, [event], Path(directory), {}, datetime(2026, 9, 7, tzinfo=TZ))
        self.assertEqual(result["mode"], "ai_degraded_stock_alpha_rules")
        self.assertNotIn("test-private", str(result))

    @patch("stock_alpha.event_ai.requests.post")
    @patch("stock_alpha.event_ai.load_ai_settings", return_value=AISettings(api_key="test-private"))
    def test_valid_review_is_cached_and_does_not_create_orders(self, settings, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"choices": [{"message": {"content": '{"opportunities":[]}'}}]}
        event = self.event(self.plan["scores"][0]["ts_code"])
        now = datetime(2026, 9, 7, tzinfo=TZ)
        with tempfile.TemporaryDirectory() as directory:
            first = review(self.plan, [event], Path(directory), {}, now)
            second = review(self.plan, [event], Path(directory), {}, now)
            self.assertFalse((Path(directory) / "paper.sqlite").exists())
        self.assertEqual(first, second)
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
