from __future__ import annotations

import json
import re
from typing import Any

from ai.llm_failover import LLMFailoverError, LLMTarget, request_with_failover
from ai.prompt_builder import build_prompt
from app.config import settings
from engine.ranking_engine import merge_signal_with_recommendations
from engine.signal_engine import generate_signal


STOCK_AI_EXECUTION_CHAIN = "deepseek_then_minimax_then_stepfun_then_hybrid_alpha_rules"


def call_minimax(
    state: dict[str, Any],
    leader: dict[str, Any],
    recommendation_bundle: dict[str, Any] | None = None,
    market_sentiment: dict[str, Any] | None = None,
    portfolio_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = build_prompt(state, leader, recommendation_bundle, market_sentiment, portfolio_plan)
    base_signal = generate_signal(state, leader, market_sentiment)
    if recommendation_bundle:
        base_signal = merge_signal_with_recommendations(base_signal, recommendation_bundle)
    if portfolio_plan:
        base_signal = {**base_signal, **portfolio_plan}
    if settings.dry_run or state.get("state") == "NO_DATA" or not leader.get("stock"):
        return {
            **base_signal,
            "ai_provider": "skipped" if state.get("state") == "NO_DATA" else "dry_run",
            "prompt": prompt,
        }

    try:
        payload, model, attempts = request_with_failover(
            endpoint=settings.stock_ai_primary_endpoint or "",
            api_key=settings.stock_ai_primary_api_key or "",
            primary_model=settings.stock_ai_primary_model,
            fallback_model=None,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            timeout=30,
            validator=_has_structured_signal,
            targets=stock_ai_targets(),
        )
        return _normalize_minimax_payload(payload, base_signal, prompt, model, attempts)
    except LLMFailoverError as exc:
        result = _ai_unavailable_signal(base_signal, prompt, "fallback_error", str(exc))
        result["ai_attempts"] = exc.attempts
        return result


class MiniMaxClient:
    def decide(
        self,
        state: dict[str, Any],
        leader: dict[str, Any],
        recommendation_bundle: dict[str, Any] | None = None,
        market_sentiment: dict[str, Any] | None = None,
        portfolio_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return call_minimax(state, leader, recommendation_bundle, market_sentiment, portfolio_plan)


def _normalize_minimax_payload(
    payload: dict[str, Any],
    fallback: dict[str, Any],
    prompt: str,
    model: str,
    attempts: list[dict[str, str]],
) -> dict[str, Any]:
    text = _extract_text(payload)
    parsed = _parse_jsonish(text)
    if not parsed:
        safe = _ai_unavailable_signal(
            fallback,
            prompt,
            settings.minimax_provider,
            "LLM response was not structured JSON.",
        )
        safe["raw"] = payload
        return safe

    signal = str(parsed.get("signal") or parsed.get("action") or fallback["signal"]).upper()
    risk_level = str(parsed.get("risk_level") or parsed.get("risk level") or fallback["risk_level"])
    reasoning = str(parsed.get("reasoning") or parsed.get("reason") or fallback["reasoning"])
    return {
        **fallback,
        "signal": signal if signal in {"BUY", "SELL", "HOLD"} else fallback["signal"],
        "position": min(_float(parsed.get("position"), fallback["position"]), _float(fallback.get("position"), 0.0)),
        "risk_level": risk_level,
        "reasoning": reasoning,
        "ai_provider": _successful_provider(attempts),
        "ai_model": model,
        "ai_fallback_used": model != settings.stock_ai_primary_model,
        "ai_attempts": attempts,
        "prompt": prompt,
        "raw": payload,
    }


def _has_structured_signal(payload: dict[str, Any]) -> bool:
    return bool(_parse_jsonish(_extract_text(payload)))


def stock_ai_targets() -> list[LLMTarget]:
    return [
        LLMTarget(
            provider="deepseek",
            endpoint=settings.stock_ai_primary_endpoint,
            api_key=settings.stock_ai_primary_api_key,
            model=settings.stock_ai_primary_model,
        ),
        LLMTarget(
            provider="minimax",
            endpoint=settings.stock_ai_secondary_endpoint,
            api_key=settings.stock_ai_secondary_api_key,
            model=settings.stock_ai_secondary_model,
        ),
        LLMTarget(
            provider="stepfun",
            endpoint=settings.stock_ai_tertiary_endpoint,
            api_key=settings.stock_ai_tertiary_api_key,
            model=settings.stock_ai_tertiary_model,
        ),
    ]


def _successful_provider(attempts: list[dict[str, str]]) -> str:
    for attempt in reversed(attempts):
        if attempt.get("status") == "ok":
            return str(attempt.get("provider") or "unknown")
    return "unknown"


def _extract_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            return str(message.get("content") or "")
        return str(choices[0].get("text") or "")
    for key in ("reply", "output_text", "content"):
        if payload.get(key):
            return str(payload[key])
    return json.dumps(payload, ensure_ascii=False)


def _parse_jsonish(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
    return {}


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ai_unavailable_signal(fallback: dict[str, Any], prompt: str, provider: str, error: str) -> dict[str, Any]:
    fallback_signal = str(fallback.get("signal") or "HOLD").upper()
    if settings.ai_degraded_fallback_enabled:
        flags = list(fallback.get("risk_flags") or [])
        flags.append("ai_degraded_rule_execution")
        rule_reasoning = str(fallback.get("reasoning") or "规则引擎未给出额外说明。")
        return {
            **fallback,
            "signal": fallback_signal,
            "reasoning": (
                "AI复核暂不可用，已跳过AI决策，按Hybrid Alpha规则引擎原信号、原仓位和硬风控执行。"
                f" 原规则结论：{rule_reasoning}"
            ),
            "risk_flags": list(dict.fromkeys(flags)),
            "ai_provider": provider,
            "prompt": prompt,
            "ai_error": error,
            "ai_degraded": True,
            "ai_execution_mode": "hybrid_alpha_rules_only",
            "ai_execution_chain": STOCK_AI_EXECUTION_CHAIN,
        }
    return {
        **fallback,
        "signal": "HOLD",
        "position": 0.0,
        "risk_level": "high",
        "reasoning": "AI decision layer unavailable or unstructured; pause positive signals.",
        "ai_provider": provider,
        "prompt": prompt,
        "ai_error": error,
    }
