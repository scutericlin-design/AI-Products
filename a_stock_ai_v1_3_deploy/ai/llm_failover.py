from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import requests


PayloadValidator = Callable[[dict[str, Any]], bool]


@dataclass(frozen=True)
class LLMTarget:
    provider: str
    endpoint: str | None
    api_key: str | None
    model: str


class LLMFailoverError(RuntimeError):
    def __init__(self, attempts: list[dict[str, str]]) -> None:
        self.attempts = attempts
        summary = "; ".join(
            f"{item.get('model', 'unknown')}: {item.get('error', 'unavailable')}"
            for item in attempts
        )
        super().__init__(summary or "No LLM attempt was made.")


def request_with_failover(
    *,
    endpoint: str,
    api_key: str,
    primary_model: str,
    fallback_model: str | None,
    messages: list[dict[str, str]],
    temperature: float,
    timeout: int,
    validator: PayloadValidator | None = None,
    targets: list[LLMTarget] | None = None,
) -> tuple[dict[str, Any], str, list[dict[str, str]]]:
    """Try ordered LLM targets; the legacy two-model arguments remain supported."""
    resolved_targets = list(
        targets
        or [
            LLMTarget("primary", endpoint, api_key, primary_model),
            *(
                [LLMTarget("fallback", endpoint, api_key, fallback_model)]
                if fallback_model and fallback_model != primary_model
                else []
            ),
        ]
    )

    attempts: list[dict[str, str]] = []
    for target in resolved_targets:
        if not target.endpoint or not target.api_key or not target.model:
            attempts.append(
                {
                    "provider": target.provider,
                    "model": target.model or "unknown",
                    "status": "skipped_missing_config",
                    "error": "endpoint, API key, or model is missing",
                }
            )
            continue
        try:
            response = requests.post(
                target.endpoint,
                headers={
                    "Authorization": f"Bearer {target.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": target.model,
                    "messages": messages,
                    "temperature": temperature,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("LLM response was not a JSON object.")
            if validator is not None and not validator(payload):
                raise ValueError("LLM response did not satisfy the required output format.")
            attempts.append(
                {"provider": target.provider, "model": target.model, "status": "ok"}
            )
            return payload, target.model, attempts
        except Exception as exc:
            attempts.append(
                {
                    "provider": target.provider,
                    "model": target.model,
                    "status": "failed",
                    "error": str(exc)[:300],
                }
            )

    raise LLMFailoverError(attempts)
