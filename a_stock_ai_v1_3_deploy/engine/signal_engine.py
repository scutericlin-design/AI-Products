from __future__ import annotations

from typing import Any

from app.config import settings
from learning.strategy_params import param_float


def generate_signal(
    state: dict[str, Any],
    leader: dict[str, Any],
    market_sentiment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market_state = str(state.get("state", "SIDEWAYS")).upper()
    sentiment_profile = market_sentiment or {}
    sentiment = int(sentiment_profile.get("sentiment_score") or state.get("sentiment") or 50)
    panic_score = float(sentiment_profile.get("panic_score") or 0)
    min_buy_score = param_float("SENTIMENT_MIN_BUY_SCORE", settings.sentiment_min_buy_score)
    risk_off_score = param_float("SENTIMENT_RISK_OFF_SCORE", settings.sentiment_risk_off_score)
    panic_threshold = param_float("SENTIMENT_PANIC_THRESHOLD", settings.sentiment_panic_threshold)
    trade_permission = str(sentiment_profile.get("trade_permission") or "BUY_ALLOWED").upper()
    risk_appetite = str(sentiment_profile.get("risk_appetite") or "neutral").lower()
    strength = int(leader.get("strength") or 0)
    leader_status = str(leader.get("status", "watch")).lower()

    if market_state == "NO_DATA" or not leader.get("stock"):
        signal = "HOLD"
        position = 0.0
        risk_level = "high"
        reasoning = "No reliable realtime data or leader candidate; perception is paused."
    elif trade_permission == "NO_BUY" or panic_score >= panic_threshold:
        signal = "SELL" if market_state == "DOWNTREND" or panic_score >= panic_threshold else "HOLD"
        position = 0.0
        risk_level = "high"
        reasoning = "Market sentiment blocks new buys; risk controls stay defensive."
    elif (
        market_state == "UPTREND"
        and sentiment >= min_buy_score
        and strength >= 80
        and leader_status == "strong"
        and risk_appetite in {"risk_on", "neutral"}
    ):
        signal = "BUY"
        position_cap = settings.max_position_weight * (0.5 if trade_permission == "LIGHT_ONLY" else 1.0)
        position = min(position_cap, 0.1)
        risk_level = "medium"
        reasoning = "Market trend, sentiment, and leader strength are aligned within risk limits."
    elif market_state == "DOWNTREND" or sentiment <= risk_off_score:
        signal = "SELL"
        position = 0.0
        risk_level = "high"
        reasoning = "Market state is weak or sentiment is below the risk threshold."
    else:
        signal = "HOLD"
        position = 0.0
        risk_level = "normal"
        reasoning = "Signal quality is not strong enough for action."

    return {
        "engine": settings.engine_positioning,
        "engine_name": settings.engine_name,
        "output_type": "market_perception_signal",
        "stock": leader.get("stock"),
        "signal": signal,
        "position": round(position, 4),
        "risk_level": risk_level,
        "reasoning": reasoning,
        "state": state,
        "market_sentiment": sentiment_profile,
        "leader": leader,
    }


class SignalEngine:
    def build_signals(
        self,
        state: dict[str, Any],
        leader: dict[str, Any],
        market_sentiment: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return generate_signal(state, leader, market_sentiment)
