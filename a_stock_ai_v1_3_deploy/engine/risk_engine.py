from __future__ import annotations

from math import isfinite
from typing import Any

from app.config import settings
from engine.position_engine import PositionEngine
from learning.strategy_params import param_float


def risk_check(ai_result: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_signal(ai_result)
    before_ai = _reference_budget(normalized)
    checked = PositionEngine().apply_position_limits(normalized)
    checked = _apply_sentiment_controls(checked)

    state = normalized.get("state")
    if isinstance(state, dict) and str(state.get("state") or "").strip().upper() == "DOWNTREND":
        checked["signal"] = "SELL"
        checked["position"] = 0.0
        checked["risk_level"] = "high"
        flags: list[str] = list(checked.get("risk_flags") or [])
        flags.append("forced_downtrend_sell")
        checked["risk_flags"] = list(dict.fromkeys(flags))

    after_risk = _weight(checked.get("position")) if checked["signal"] == "BUY" else 0.0
    scale = min(1.0, after_risk / before_ai) if before_ai > 0 else 0.0
    checked = _apply_recommendation_controls(checked, scale)
    checked["risk_budget"] = {
        "version": 1,
        "semantics": "relative_candidate_budget_not_total_portfolio_exposure",
        "before_ai": before_ai,
        "after_ai": normalized["position"],
        "after_risk": checked["position"],
        "candidate_weight_scale": scale,
    }
    return checked


def _reference_budget(signal: dict[str, Any]) -> float:
    previous = signal.get("risk_budget")
    if isinstance(previous, dict) and previous.get("version") == 1:
        # Rechecking an already scaled signal must not apply its reduction twice.
        return _weight(previous.get("after_risk"))
    total = sum(_candidate_weight(item) for item in signal.get("recommendations") or [] if isinstance(item, dict))
    # Ranking caps the bundle scalar, not its individual target weights. The AI
    # merge changes only that scalar, so reconstruct its pre-AI value here.
    # The multi-strategy merge instead emits the uncapped aggregate target.
    return round(total if signal.get("is_multi_strategy") else min(total, settings.max_position_weight), 4)


class RiskEngine:
    def apply_risk_controls(self, signal: dict[str, Any]) -> dict[str, Any]:
        return risk_check(signal)


def _normalize_signal(value: dict[str, Any]) -> dict[str, Any]:
    if "choices" in value and "signal" not in value:
        return {
            "signal": "HOLD",
            "position": 0.0,
            "risk_level": "unknown",
            "reasoning": "MiniMax returned raw response without normalized signal.",
            "raw": value,
        }
    normalized = dict(value)
    normalized["signal"] = str(normalized.get("signal", "HOLD")).strip().upper()
    if normalized["signal"] not in {"BUY", "SELL", "HOLD"}:
        normalized["signal"] = "HOLD"
    normalized["position"] = _weight(normalized.get("position"))
    normalized.setdefault("risk_level", "normal")
    normalized.setdefault("reasoning", "")
    return normalized


def _apply_recommendation_controls(signal: dict[str, Any], weight_scale: float = 1.0) -> dict[str, Any]:
    updated = dict(signal)
    overall_signal = str(updated.get("signal", "HOLD")).upper()
    recommendations = [item for item in updated.get("recommendations") or [] if isinstance(item, dict)]
    if overall_signal != "BUY":
        if recommendations:
            updated["watchlist"] = _downgrade_to_watchlist(recommendations) + list(updated.get("watchlist") or [])
        updated["recommendations"] = []
        updated["recommendation_count"] = 0
        updated["position"] = 0.0 if overall_signal in {"HOLD", "SELL"} else updated.get("position", 0.0)
        updated.setdefault("no_recommendation_reason", "AI或风控未确认买入，推荐降级为观察")
        return updated

    safe_recommendations = []
    downgraded = []
    for item in recommendations:
        weight = round(_candidate_weight(item) * weight_scale, 8)
        if weight > 0 and item.get("can_buy") and not item.get("is_limit_up") and not item.get("near_limit_up"):
            candidate = {**item, "position": weight}
            if "target_weight" in item:
                candidate["target_weight"] = weight
            if isinstance(item.get("strategy_weights"), dict):
                candidate["strategy_weights"] = {
                    key: round(_weight(value) * weight_scale, 8) for key, value in item["strategy_weights"].items()
                }
            safe_recommendations.append(candidate)
        else:
            downgraded.append({**item, "action": "WATCH", "position": 0.0})
    updated["recommendations"] = safe_recommendations
    updated["recommendation_count"] = len(safe_recommendations)
    if downgraded:
        updated["watchlist"] = downgraded + list(updated.get("watchlist") or [])
    if not safe_recommendations:
        updated["signal"] = "HOLD"
        updated["position"] = 0.0
        updated["no_recommendation_reason"] = "风控过滤后没有可买入标的"
    else:
        updated["position"] = PositionEngine().apply_position_limits(updated)["position"]
    return updated


def _apply_sentiment_controls(signal: dict[str, Any]) -> dict[str, Any]:
    sentiment = signal.get("market_sentiment")
    if not isinstance(sentiment, dict) or not sentiment:
        return signal

    updated = dict(signal)
    trade_permission = str(sentiment.get("trade_permission") or "BUY_ALLOWED").upper()
    panic_score = _float(sentiment.get("panic_score"), 0.0)
    panic_threshold = param_float("SENTIMENT_PANIC_THRESHOLD", settings.sentiment_panic_threshold)
    flags: list[str] = list(updated.get("risk_flags") or [])

    if trade_permission == "NO_BUY" or panic_score >= panic_threshold:
        updated["signal"] = "SELL" if panic_score >= panic_threshold else "HOLD"
        updated["position"] = 0.0
        updated["risk_level"] = "high"
        updated["no_recommendation_reason"] = (
            updated.get("no_recommendation_reason")
            or "市场情绪风控禁止新增买入，系统保持防守"
        )
        flags.append("sentiment_forced_defensive")
    elif trade_permission == "LIGHT_ONLY" and str(updated.get("signal", "HOLD")).upper() == "BUY":
        updated["position"] = min(
            _float(updated.get("position"), 0.0),
            settings.max_position_weight * 0.5,
        )
        if str(updated.get("risk_level", "normal")).lower() == "normal":
            updated["risk_level"] = "medium"
        flags.append("sentiment_light_position")

    updated["risk_flags"] = list(dict.fromkeys(flags))
    return updated


def _float(value: Any, default: float) -> float:
    try:
        number = float(value)
        return number if isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _weight(value: Any) -> float:
    return max(_float(value, 0.0), 0.0)


def _candidate_weight(item: dict[str, Any]) -> float:
    return _weight(item.get("position", item.get("target_weight")))


def _downgrade_to_watchlist(recommendations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**item, "action": "WATCH", "position": 0.0} for item in recommendations]
