from __future__ import annotations

from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import requests

from etf_strategy.ai_reviewer import ETFMiniMaxReviewer, apply_ai_review_to_plan
from etf_strategy.config import ETFStrategySettings
from etf_strategy.models import ETFDecisionPlan


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class ETFLLMFailoverTests(TestCase):
    def test_reviewer_uses_minimax_after_stepfun_timeout(self) -> None:
        root = Path("/tmp")
        settings = ETFStrategySettings(
            project_root=root,
            storage_dir=root,
            db_path=root / "etf.sqlite",
            reports_dir=root / "reports",
            tushare_token=None,
            tushare_base_url=None,
            minimax_api_key="test-key",
            minimax_endpoint="https://example.test/v1/chat/completions",
            minimax_model="stepfun-ai/step-3.7-flash",
            ai_enabled=True,
            request_pause_seconds=0.0,
            daily_amount_multiplier=1000.0,
            min_avg_turnover_yuan=0.0,
            turnover_to_order_multiple=1.0,
            max_nav_premium_pct=0.03,
            minimax_fallback_model="minimaxai/minimax-m3",
        )
        response = _Response(
            {
                "choices": [
                    {"message": {"content": '{"verdict":"APPROVE","risk_flags":[],"reasoning":"fallback ok"}'}}
                ]
            }
        )
        with patch(
            "ai.llm_failover.requests.post",
            side_effect=[requests.Timeout("primary timeout"), response],
        ) as post:
            result = ETFMiniMaxReviewer(settings).review({"target": {"symbol": "510300.SH", "name": "沪深300ETF"}})

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["verdict"], "APPROVE")
        self.assertEqual(result["ai_model"], "minimaxai/minimax-m3")
        self.assertTrue(result["ai_fallback_used"])
        self.assertEqual(
            [call.kwargs["json"]["model"] for call in post.call_args_list],
            ["stepfun-ai/step-3.7-flash", "minimaxai/minimax-m3"],
        )

    def test_unavailable_ai_preserves_the_rule_plan(self) -> None:
        root = Path("/tmp")
        settings = ETFStrategySettings(
            project_root=root,
            storage_dir=root,
            db_path=root / "etf.sqlite",
            reports_dir=root / "reports",
            tushare_token=None,
            tushare_base_url=None,
            minimax_api_key=None,
            minimax_endpoint=None,
            minimax_model="stepfun-ai/step-3.7-flash",
            ai_enabled=True,
            request_pause_seconds=0.0,
            daily_amount_multiplier=1000.0,
            min_avg_turnover_yuan=0.0,
            turnover_to_order_multiple=1.0,
            max_nav_premium_pct=0.03,
        )
        plan = ETFDecisionPlan(
            strategy_id="wufu_v7_static",
            strategy_version="test",
            as_of="20260727",
            regime="trend",
            regime_detail={},
            signal="BUY",
            target_weight=0.65,
            target={"symbol": "510300.SH", "name": "沪深300ETF"},
            current_symbol=None,
            trade_plan=[{"side": "BUY", "target_weight": 0.65}],
            candidates=[],
            reasoning="规则引擎候选通过。",
        )

        review = ETFMiniMaxReviewer(settings).review(plan.to_dict())
        apply_ai_review_to_plan(plan, review)

        self.assertEqual(review["status"], "degraded")
        self.assertEqual(review["verdict"], "NOT_REVIEWED")
        self.assertTrue(review["ai_degraded"])
        self.assertEqual(plan.signal, "BUY")
        self.assertEqual(plan.target_weight, 0.65)
        self.assertEqual(plan.trade_plan, [{"side": "BUY", "target_weight": 0.65}])
        self.assertEqual(plan.execution_mode, "etf_strategy_rules_only")
        self.assertIn("AI降级：ETF规则引擎独立执行", plan.risk_flags)
