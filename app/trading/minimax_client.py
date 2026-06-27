from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any

import requests

from app.config import settings
from app.trading.types import Decision, LeaderCandidate, MarketState
from app.trading.utils import safe_float


class MiniMaxDecisionClient:
    def decide(self, market_state: MarketState, leaders: list[LeaderCandidate]) -> list[Decision]:
        if not leaders:
            return []
        if not self._should_call_api():
            return self._heuristic_decisions(market_state, leaders)

        prompt = self._build_prompt(market_state, leaders)
        try:
            response = requests.post(
                settings.trading_minimax_endpoint,
                headers={
                    "Authorization": f"Bearer {settings.trading_minimax_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.trading_minimax_model,
                    "messages": [
                        {"role": "system", "content": "Return only strict JSON for intraday A-share decisions."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                },
                timeout=20,
            )
            response.raise_for_status()
            return self._parse_response(response.json(), leaders)
        except Exception as exc:
            fallback = self._heuristic_decisions(market_state, leaders)
            return [
                Decision(
                    symbol=item.symbol,
                    name=item.name,
                    action=item.action,
                    confidence=item.confidence,
                    target_weight=item.target_weight,
                    reason=f"{item.reason} MiniMax fallback: {exc}",
                    risk_level=item.risk_level,
                    risk_flags=item.risk_flags + ("minimax_fallback",),
                    raw=item.raw,
                )
                for item in fallback
            ]

    def _should_call_api(self) -> bool:
        return bool(
            settings.trading_minimax_api_key
            and settings.trading_minimax_endpoint
            and (not settings.trading_dry_run)
        )

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

    def _parse_response(self, payload: dict[str, Any], leaders: list[LeaderCandidate]) -> list[Decision]:
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
                    raw={"mode": "minimax", "response": payload},
                )
            )
        return decisions or self._heuristic_decisions(
            MarketState("unknown", "neutral", 0, 0, 0, len(leaders), "MiniMax response had no decisions."),
            leaders,
        )

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

