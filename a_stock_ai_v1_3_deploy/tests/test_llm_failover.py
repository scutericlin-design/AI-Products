from __future__ import annotations

from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

import requests

from ai.llm_failover import request_with_failover
from ai.minimax_client import call_minimax
from app.config import settings
from learning.minimax_parameter_reviewer import review_parameter_proposal


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class LLMFailoverTests(TestCase):
    def test_uses_secondary_model_after_primary_timeout(self) -> None:
        secondary = _Response({"ok": True})
        with patch(
            "ai.llm_failover.requests.post",
            side_effect=[requests.Timeout("primary timeout"), secondary],
        ) as post:
            payload, model, attempts = request_with_failover(
                endpoint="https://example.test/v1/chat/completions",
                api_key="test-key",
                primary_model="stepfun-ai/step-3.7-flash",
                fallback_model="minimaxai/minimax-m3",
                messages=[{"role": "user", "content": "test"}],
                temperature=0.0,
                timeout=1,
                validator=lambda item: item.get("ok") is True,
            )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(model, "minimaxai/minimax-m3")
        self.assertEqual([item["status"] for item in attempts], ["failed", "ok"])
        self.assertEqual(
            [call.kwargs["json"]["model"] for call in post.call_args_list],
            ["stepfun-ai/step-3.7-flash", "minimaxai/minimax-m3"],
        )

    def test_stock_decision_uses_deepseek_then_minimax_then_stepfun(self) -> None:
        primary = _Response({"choices": [{"message": {"content": "not json"}}]})
        tertiary = _Response(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"signal":"BUY","position":0.04,"risk_level":"medium","reasoning":"third provider ok"}'
                        }
                    }
                ]
            }
        )
        with patch(
            "ai.minimax_client.settings",
            replace(
                settings,
                dry_run=False,
                stock_ai_primary_api_key="deepseek-key",
                stock_ai_primary_endpoint="https://deepseek.test/v1/chat/completions",
                stock_ai_primary_model="deepseek-v4-flash-0731",
                stock_ai_secondary_api_key="minimax-key",
                stock_ai_secondary_endpoint="https://minimax.test/v1/chat/completions",
                stock_ai_secondary_model="minimaxai/minimax-m3",
                stock_ai_tertiary_api_key="stepfun-key",
                stock_ai_tertiary_endpoint="https://stepfun.test/v1/chat/completions",
                stock_ai_tertiary_model="stepfun-ai/step-3.7-flash",
            ),
        ), patch(
            "ai.llm_failover.requests.post",
            side_effect=[primary, requests.Timeout("minimax timeout"), tertiary],
        ) as post:
            result = call_minimax(
                {"state": "UPTREND", "sentiment": 66, "volume": "high"},
                {"stock": "600000.SH", "name": "浦发银行", "strength": 85, "status": "strong"},
                {
                    "signal": "BUY",
                    "position": 0.05,
                    "risk_level": "medium",
                    "recommendations": [{"symbol": "600000.SH", "can_buy": True, "position": 0.05}],
                },
                {"sentiment_score": 66, "trade_permission": "BUY_ALLOWED", "risk_appetite": "risk_on"},
        )

        self.assertEqual(result["signal"], "BUY")
        self.assertEqual(result["ai_provider"], "stepfun")
        self.assertEqual(result["ai_model"], "stepfun-ai/step-3.7-flash")
        self.assertTrue(result["ai_fallback_used"])
        self.assertFalse(result.get("ai_degraded", False))
        self.assertEqual(
            [item["provider"] for item in result["ai_attempts"]],
            ["deepseek", "minimax", "stepfun"],
        )
        self.assertEqual(
            [call.kwargs["json"]["model"] for call in post.call_args_list],
            ["deepseek-v4-flash-0731", "minimaxai/minimax-m3", "stepfun-ai/step-3.7-flash"],
        )
        self.assertEqual(
            [call.args[0] for call in post.call_args_list],
            [
                "https://deepseek.test/v1/chat/completions",
                "https://minimax.test/v1/chat/completions",
                "https://stepfun.test/v1/chat/completions",
            ],
        )

    def test_stock_parameter_review_uses_the_same_deepseek_primary_chain(self) -> None:
        response = _Response(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"decision":"HOLD","confidence":0.99,"changes":{},"reasoning":"parameters remain stable"}'
                        }
                    }
                ]
            }
        )
        test_settings = replace(
            settings,
            dry_run=False,
            self_learning_ai_review_enabled=True,
            stock_ai_primary_api_key="deepseek-key",
            stock_ai_primary_endpoint="https://deepseek.test/v1/chat/completions",
            stock_ai_primary_model="deepseek-v4-flash-0731",
            stock_ai_secondary_api_key="minimax-key",
            stock_ai_secondary_endpoint="https://minimax.test/v1/chat/completions",
            stock_ai_secondary_model="minimaxai/minimax-m3",
            stock_ai_tertiary_api_key="stepfun-key",
            stock_ai_tertiary_endpoint="https://stepfun.test/v1/chat/completions",
            stock_ai_tertiary_model="stepfun-ai/step-3.7-flash",
        )
        with patch("ai.minimax_client.settings", test_settings), patch(
            "learning.minimax_parameter_reviewer.settings", test_settings
        ), patch("ai.llm_failover.requests.post", return_value=response) as post:
            result = review_parameter_proposal(
                {"outcomes": []},
                {"metrics": {}, "changes": {}, "reason": "no change proposed"},
            )

        self.assertEqual(result["ai_model"], "deepseek-v4-flash-0731")
        self.assertEqual(result["ai_attempts"][0]["provider"], "deepseek")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "deepseek-v4-flash-0731")
