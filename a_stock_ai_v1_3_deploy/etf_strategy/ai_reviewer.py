from __future__ import annotations

import json
import re
from typing import Any

from ai.llm_failover import LLMFailoverError, request_with_failover
from etf_strategy.config import ETFStrategySettings


ETF_AI_RULES_ONLY_MODE = "etf_strategy_rules_only"
ETF_AI_EXECUTION_CHAIN = "stepfun_then_minimax_then_etf_strategy_rules"


class ETFMiniMaxReviewer:
    """Optional AI veto layer for a fully determined quantitative plan.

    AI is deliberately not allowed to nominate a symbol, enlarge a position, or
    turn a hard-rejected candidate into a trade. It can only approve or veto the
    already-selected local plan and provide an explanation for the user.
    """

    def __init__(self, settings: ETFStrategySettings):
        self.settings = settings

    def review(self, plan: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.ai_enabled:
            return {"status": "disabled", "verdict": "NOT_REVIEWED"}
        if not self.settings.minimax_api_key or not self.settings.minimax_endpoint:
            return _rule_only_review(
                "fallback_missing_config",
                "LLM review configuration is missing.",
            )
        prompt = _build_prompt(plan)
        try:
            payload, model, attempts = request_with_failover(
                endpoint=self.settings.minimax_endpoint,
                api_key=self.settings.minimax_api_key,
                primary_model=self.settings.minimax_model,
                fallback_model=self.settings.minimax_fallback_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                timeout=30,
                validator=_has_structured_review,
            )
            parsed = _parse_response(payload)
            verdict = str(parsed.get("verdict") or "HOLD").upper()
            if verdict not in {"APPROVE", "HOLD", "REJECT"}:
                verdict = "HOLD"
            return {
                "status": "ok",
                "verdict": verdict,
                "reasoning": str(parsed.get("reasoning") or ""),
                "risk_flags": _string_list(parsed.get("risk_flags")),
                "raw": parsed,
                "ai_model": model,
                "ai_fallback_used": model != self.settings.minimax_model,
                "ai_attempts": attempts,
            }
        except LLMFailoverError as exc:
            return _rule_only_review(
                "fallback_error",
                f"LLM review failed: {exc}",
                attempts=exc.attempts,
            )


def apply_ai_review_to_plan(plan: Any, review: dict[str, Any]) -> None:
    """Apply a successful veto only; failed AI checks preserve the local plan."""
    plan.ai_review = review
    verdict = str(review.get("verdict") or "NOT_REVIEWED").upper()
    if plan.signal in {"BUY", "SWITCH"} and review.get("status") == "ok" and verdict != "APPROVE":
        plan.signal = "HOLD"
        plan.target_weight = 0.0
        plan.trade_plan = []
        plan.risk_flags.append("AI风险复核未批准，维持现金")
        plan.reasoning = "量化候选存在，但AI仅作为否决层未批准本次计划。"
        return

    if review.get("ai_degraded"):
        if "AI降级：ETF规则引擎独立执行" not in plan.risk_flags:
            plan.risk_flags.append("AI降级：ETF规则引擎独立执行")
        plan.execution_mode = ETF_AI_RULES_ONLY_MODE
        suffix = "AI复核不可用，保留ETF策略原信号、原仓位和既有硬风控执行。"
        if suffix not in plan.reasoning:
            plan.reasoning = f"{plan.reasoning} {suffix}".strip()


def execution_context_from_plan(plan: Any) -> dict[str, Any]:
    """Return the audit fields that must travel with every degraded fill."""
    review = plan.ai_review if isinstance(getattr(plan, "ai_review", None), dict) else {}
    if not review.get("ai_degraded"):
        return {}
    return {
        "ai_degraded": True,
        "ai_execution_mode": str(review.get("ai_execution_mode") or ETF_AI_RULES_ONLY_MODE),
        "ai_execution_chain": str(review.get("ai_execution_chain") or ETF_AI_EXECUTION_CHAIN),
        "ai_provider": str(review.get("ai_provider") or "fallback_error"),
        "ai_error": str(review.get("ai_error") or review.get("reasoning") or ""),
    }


def _rule_only_review(
    provider: str,
    error: str,
    *,
    attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "degraded",
        "verdict": "NOT_REVIEWED",
        "reasoning": "AI复核不可用，保留ETF策略原信号、原仓位和既有硬风控执行。",
        "ai_degraded": True,
        "ai_execution_mode": ETF_AI_RULES_ONLY_MODE,
        "ai_execution_chain": ETF_AI_EXECUTION_CHAIN,
        "ai_provider": provider,
        "ai_error": error,
    }
    if attempts is not None:
        result["ai_attempts"] = attempts
    return result


def _build_prompt(plan: dict[str, Any]) -> str:
    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    return f"""
你是ETF交易计划的风险复核员，不是选股或下单机器人。
只能根据下方系统已经确定的数据进行复核：不得新增ETF代码，不得提高仓位，不得推翻流动性、趋势、折溢价和市场状态硬规则。

量化计划：
{json.dumps(plan, ensure_ascii=False)}

重点检查：市场状态是否与目标ETF类别相符、跨境/商品ETF是否可能存在折溢价或时差风险、仓位是否与风险一致。
目标ETF：{target.get('symbol')} {target.get('name')}

严格只输出JSON：
{{"verdict":"APPROVE|HOLD|REJECT","risk_flags":["..."],"reasoning":"不超过80字"}}
"""


def _parse_response(payload: dict[str, Any]) -> dict[str, Any]:
    text = ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        text = str(message.get("content") or first.get("text") or "")
    if not text:
        text = str(payload.get("content") or payload.get("reply") or "")
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}


def _has_structured_review(payload: dict[str, Any]) -> bool:
    return bool(_parse_response(payload))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value:
        return [str(value)]
    return []
