from __future__ import annotations

import json
import re
from typing import Any

import requests

from ai.prompt_builder import build_prompt
from app.config import settings
from engine.ranking_engine import merge_signal_with_recommendations
from engine.signal_engine import generate_signal


def call_minimax(
    state: dict[str, Any],
    leader: dict[str, Any],
    recommendation_bundle: dict[str, Any] | None = None,
    market_sentiment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = build_prompt(state, leader, recommendation_bundle, market_sentiment)
    base_signal = generate_signal(state, leader, market_sentiment)
    if recommendation_bundle:
        base_signal = merge_signal_with_recommendations(base_signal, recommendation_bundle)
    if settings.dry_run or state.get("state") == "NO_DATA" or not leader.get("stock"):
        return {
            **base_signal,
            "ai_provider": "skipped" if state.get("state") == "NO_DATA" else "dry_run",
            "prompt": prompt,
        }

    if not settings.minimax_api_key or not settings.minimax_endpoint:
        return _ai_unavailable_signal(
            base_signal,
            prompt,
            "fallback_missing_config",
            "MiniMax API key or endpoint is not configured.",
        )

    try:
        response = requests.post(
            settings.minimax_endpoint,
            headers={
                "Authorization": f"Bearer {settings.minimax_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.minimax_model,
                "messages": [
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        return _normalize_minimax_payload(payload, base_signal, prompt)
    except Exception as exc:
        return _ai_unavailable_signal(base_signal, prompt, "fallback_error", str(exc))


class MiniMaxClient:
    def decide(
        self,
        state: dict[str, Any],
        leader: dict[str, Any],
        recommendation_bundle: dict[str, Any] | None = None,
        market_sentiment: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return call_minimax(state, leader, recommendation_bundle, market_sentiment)


def _normalize_minimax_payload(payload: dict[str, Any], fallback: dict[str, Any], prompt: str) -> dict[str, Any]:
    text = _extract_text(payload)
    parsed = _parse_jsonish(text)
    if not parsed:
        safe = _ai_unavailable_signal(
            fallback,
            prompt,
            settings.minimax_provider,
            "MiniMax response was not structured JSON.",
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
        "ai_provider": settings.minimax_provider,
        "prompt": prompt,
        "raw": payload,
    }


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
