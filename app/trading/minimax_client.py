from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any

import requests

from app.config import settings
from app.trading.types import Decision, LeaderCandidate, MarketState
from app.trading.utils import safe_float


AI_RULES_ONLY_MODE = "main_strategy_rules_only"
AI_DECISION_CHAIN = "stepfun_then_minimax_then_main_strategy_rules"


class MiniMaxDecisionClient:
    def decide(self, market_state: MarketState, leaders: list[LeaderCandidate]) -> list[Decision]:
        if not leaders:
            return []
        if not self._should_call_api():
            return self._rule_only_decisions(
                market_state,
                leaders,
                provider="fallback_missing_config" if not settings.trading_dry_run else "dry_run",
                error="AI decision endpoint or key is missing, or trading dry-run mode is enabled.",
            )

        prompt = self._build_prompt(market_state, leaders)
        attempts: list[dict[str, str]] = []
        last_error = ""
        for model in self._model_chain():
            try:
                response = requests.post(
                    settings.trading_minimax_endpoint,
                    headers={
                        "Authorization": f"Bearer {settings.trading_minimax_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "Return only strict JSON for intraday A-share decisions."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.2,
                    },
                    timeout=20,
                )
                response.raise_for_status()
                decisions = self._parse_response(response.json(), leaders, model, attempts)
                if decisions:
                    attempts.append({"model": model, "status": "ok"})
                    return [
                        Decision(
                            symbol=item.symbol,
                            name=item.name,
                            action=item.action,
                            confidence=item.confidence,
                            target_weight=item.target_weight,
                            reason=item.reason,
                            risk_level=item.risk_level,
                            risk_flags=item.risk_flags,
                            raw={**item.raw, "ai_attempts": attempts},
                        )
                        for item in decisions
                    ]
                raise ValueError("AI response had no usable decisions.")
            except Exception as exc:
                last_error = str(exc)
                attempts.append({"model": model, "status": "failed", "error": last_error[:300]})

        return self._rule_only_decisions(
            market_state,
            leaders,
            provider="fallback_error",
            error=last_error or "All AI decision attempts failed.",
            attempts=attempts,
        )

    def _should_call_api(self) -> bool:
        return bool(
            settings.trading_minimax_api_key
            and settings.trading_minimax_endpoint
            and (not settings.trading_dry_run)
        )

    def _model_chain(self) -> list[str]:
        models = [settings.trading_minimax_model]
        fallback = settings.trading_minimax_fallback_model
        if fallback and fallback not in models:
            models.append(fallback)
        return [model for model in models if model]

    def _heuristic_decisions(self, market_state: MarketState, leaders: list[LeaderCandidate]) -> list[Decision]:
        decisions: list[Decision] = []
        for leader in leaders:
            if market_state.regime in {"risk_off", "no_data", "thin_liquidity"}:
                action = "watch"
                confidence = 0.52
                target_weight = 0.0
                reason = f"Market regime is {market_state.regime}; keep candidate on watch."
            elif leader.pct_change >= 2.0 and leader.leader_score >= 8:
                action = "trial_buy"
                confidence = min(0.86, 0.58 + leader.leader_score / 60)
                target_weight = min(settings.trading_max_position_weight, 0.06)
                reason = "Momentum and liquidity place this symbol in the realtime leader set."
            else:
                action = "watch"
                confidence = 0.56
                target_weight = 0.0
                reason = "Candidate is visible but does not meet the realtime action threshold."

            decisions.append(
                Decision(
                    symbol=leader.symbol,
                    name=leader.name,
                    action=action,
                    confidence=round(confidence, 4),
                    target_weight=round(target_weight, 4),
                    reason=reason,
                    raw={"mode": "heuristic", "leader": asdict(leader), "market_state": asdict(market_state)},
                )
            )
        return decisions

    def _rule_only_decisions(
        self,
        market_state: MarketState,
        leaders: list[LeaderCandidate],
        *,
        provider: str,
        error: str,
        attempts: list[dict[str, str]] | None = None,
    ) -> list[Decision]:
        fallback = self._heuristic_decisions(market_state, leaders)
        return [
            Decision(
                symbol=item.symbol,
                name=item.name,
                action=item.action,
                confidence=item.confidence,
                target_weight=item.target_weight,
                reason=(
                    "AI决策链 StepFun -> MiniMax 暂不可用，主策略引擎独立执行原动作、原仓位和硬风控。"
                    f" 原规则结论：{item.reason}"
                ),
                risk_level=item.risk_level,
                risk_flags=tuple(
                    dict.fromkeys(
                        item.risk_flags
                        + (
                            "ai_degraded_main_strategy_execution",
                            "stepfun_minimax_unavailable",
                        )
                    )
                ),
                raw={
                    **item.raw,
                    "ai_degraded": True,
                    "ai_provider": provider,
                    "ai_error": error,
                    "ai_execution_mode": AI_RULES_ONLY_MODE,
                    "ai_execution_chain": AI_DECISION_CHAIN,
                    "ai_attempts": attempts or [],
                },
            )
            for item in fallback
        ]

    def _build_prompt(self, market_state: MarketState, leaders: list[LeaderCandidate]) -> str:
        payload = {
            "market_state": asdict(market_state),
            "leaders": [asdict(item) for item in leaders],
            "required_schema": {
                "decisions": [
                    {
                        "symbol": "000001.SZ",
                        "action": "trial_buy|watch|reduce|avoid",
                        "confidence": 0.0,
                        "target_weight": 0.0,
                        "reason": "short reason",
                    }
                ]
            },
        }
        return json.dumps(payload, ensure_ascii=False)

    def _parse_response(
        self,
        payload: dict[str, Any],
        leaders: list[LeaderCandidate],
        model: str,
        attempts: list[dict[str, str]],
    ) -> list[Decision]:
        text = self._extract_text(payload)
        parsed = self._extract_json(text)
        leader_by_symbol = {item.symbol: item for item in leaders}
        decisions: list[Decision] = []
        for item in parsed.get("decisions", []):
            symbol = str(item.get("symbol", "")).upper()
            leader = leader_by_symbol.get(symbol)
            if leader is None:
                continue
            decisions.append(
                Decision(
                    symbol=leader.symbol,
                    name=leader.name,
                    action=str(item.get("action") or "watch"),
                    confidence=safe_float(item.get("confidence"), 0.5),
                    target_weight=safe_float(item.get("target_weight"), 0.0),
                    reason=str(item.get("reason") or ""),
                    raw={
                        "mode": "ai_decision",
                        "ai_model": model,
                        "ai_fallback_used": model != settings.trading_minimax_model,
                        "ai_execution_chain": AI_DECISION_CHAIN,
                        "ai_attempts": attempts,
                        "response": payload,
                    },
                )
            )
        return decisions

    def _extract_text(self, payload: dict[str, Any]) -> str:
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

    def _extract_json(self, text: str) -> dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.S)
            if match:
                return json.loads(match.group(0))
        return {"decisions": []}
