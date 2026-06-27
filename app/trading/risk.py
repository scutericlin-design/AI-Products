from __future__ import annotations

from dataclasses import replace

from app.config import settings
from app.trading.types import Decision, LeaderCandidate, MarketState


class RiskEngine:
    def review(
        self,
        decisions: list[Decision],
        market_state: MarketState,
        leaders: list[LeaderCandidate],
    ) -> list[Decision]:
        leaders_by_symbol = {item.symbol: item for item in leaders}
        return [self._review_one(decision, market_state, leaders_by_symbol.get(decision.symbol)) for decision in decisions]

    def _review_one(
        self,
        decision: Decision,
        market_state: MarketState,
        leader: LeaderCandidate | None,
    ) -> Decision:
        flags = list(decision.risk_flags)
        action = decision.action
        target_weight = min(max(decision.target_weight, 0.0), settings.trading_max_position_weight)
        risk_level = decision.risk_level

        if action in {"buy", "trial_buy", "add"} and decision.confidence < settings.trading_confidence_threshold:
            action = "watch"
            target_weight = 0.0
            risk_level = "elevated"
            flags.append("low_confidence")

        if action in {"buy", "trial_buy", "add"} and market_state.regime in {"risk_off", "thin_liquidity", "no_data"}:
            action = "watch"
            target_weight = 0.0
            risk_level = "high"
            flags.append(f"market_{market_state.regime}")

        if leader is not None and action in {"buy", "trial_buy", "add"}:
            if leader.pct_change >= 9.2:
                action = "watch"
                target_weight = 0.0
                risk_level = "high"
                flags.append("limit_up_chase_risk")
            if leader.amount_yi < settings.trading_min_turnover_yi:
                action = "watch"
                target_weight = 0.0
                risk_level = "elevated"
                flags.append("insufficient_liquidity")

        reason = decision.reason
        if flags:
            reason = f"{reason} Risk flags: {','.join(dict.fromkeys(flags))}."
        return replace(
            decision,
            action=action,
            target_weight=round(target_weight, 4),
            risk_level=risk_level,
            risk_flags=tuple(dict.fromkeys(flags)),
            reason=reason,
        )

