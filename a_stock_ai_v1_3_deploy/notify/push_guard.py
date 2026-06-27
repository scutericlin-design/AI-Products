from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings
from learning.strategy_params import param_int
from storage.logger import get_push_dedupe_state, upsert_push_dedupe_state


INTRADAY_SCOPE = "feishu_intraday"
HEARTBEAT_SCOPE_PREFIX = "feishu_heartbeat"
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class PushDecision:
    should_send: bool
    reason: str
    fingerprint: str
    summary: dict[str, Any]
    periodic_summary: bool = False


def evaluate_intraday_push(final_signal: dict[str, Any]) -> PushDecision:
    summary = _build_summary(final_signal)
    fingerprint = _fingerprint(summary)
    if not settings.push_dedup_enabled:
        return PushDecision(True, "dedup_disabled", fingerprint, summary)

    previous = get_push_dedupe_state(INTRADAY_SCOPE)
    if previous is None:
        return PushDecision(True, "first_intraday_push", fingerprint, summary)

    previous_summary = _loads(previous.get("summary_json"))
    reason = _material_change_reason(previous_summary, summary)
    if reason:
        return PushDecision(True, reason, fingerprint, summary)

    if _summary_interval_reached(previous.get("last_sent_at")):
        return PushDecision(True, "periodic_status_summary", fingerprint, summary, periodic_summary=True)

    return PushDecision(False, "duplicate_intraday_signal", fingerprint, summary)


def mark_intraday_push_sent(decision: PushDecision) -> None:
    if settings.push_dedup_enabled:
        upsert_push_dedupe_state(INTRADAY_SCOPE, decision.fingerprint, decision.summary)


def should_send_heartbeat(final_signal: dict[str, Any]) -> PushDecision:
    summary = _build_summary(final_signal)
    phase = _heartbeat_phase(final_signal)
    if not settings.push_heartbeat_enabled or phase is None:
        return PushDecision(False, "heartbeat_disabled_or_not_trading_session", "", summary)

    today = datetime.now(BEIJING_TZ).date().isoformat()
    scope = f"{HEARTBEAT_SCOPE_PREFIX}:{today}:{phase}"
    previous = get_push_dedupe_state(scope)
    fingerprint = _fingerprint({"scope": scope, "summary": summary})
    if previous is not None and not _heartbeat_interval_reached(previous.get("last_sent_at")):
        return PushDecision(False, "heartbeat_interval_not_reached", fingerprint, summary)
    return PushDecision(True, f"heartbeat_{phase}", fingerprint, {**summary, "heartbeat_scope": scope})


def mark_heartbeat_sent(decision: PushDecision) -> None:
    scope = str(decision.summary.get("heartbeat_scope") or "")
    if scope:
        upsert_push_dedupe_state(scope, decision.fingerprint, decision.summary)


def _build_summary(final_signal: dict[str, Any]) -> dict[str, Any]:
    state = final_signal.get("state") if isinstance(final_signal.get("state"), dict) else {}
    sentiment = (
        final_signal.get("market_sentiment")
        if isinstance(final_signal.get("market_sentiment"), dict)
        else {}
    )
    return {
        "signal": str(final_signal.get("signal", "HOLD")).upper(),
        "risk_level": str(final_signal.get("risk_level", "normal")).lower(),
        "market_state": str(state.get("state", "unknown")).upper(),
        "sentiment_status": str(sentiment.get("sentiment_status", "unknown")).lower(),
        "trade_permission": str(sentiment.get("trade_permission", "unknown")).upper(),
        "position": _round_float(final_signal.get("position"), 4),
        "recommendations": [_recommendation_summary(item) for item in final_signal.get("recommendations") or []],
        "watchlist_symbols": [
            str(item.get("symbol"))
            for item in list(final_signal.get("watchlist") or [])[: param_int("MAX_PUSH_STOCKS", settings.max_push_stocks)]
            if item.get("symbol")
        ],
        "no_recommendation_reason": str(final_signal.get("no_recommendation_reason") or ""),
    }


def _recommendation_summary(item: dict[str, Any]) -> dict[str, Any]:
    buy_range = item.get("buy_range") if isinstance(item.get("buy_range"), dict) else {}
    return {
        "symbol": str(item.get("symbol") or ""),
        "name": str(item.get("name") or ""),
        "action": str(item.get("action") or item.get("signal") or "BUY").upper(),
        "current_price": _round_float(item.get("current_price"), 2),
        "buy_range_low": _round_float(buy_range.get("low"), 2),
        "buy_range_high": _round_float(buy_range.get("high"), 2),
        "max_buy_price": _round_float(item.get("max_buy_price"), 2),
        "stop_loss": _round_float(item.get("stop_loss"), 2),
    }


def _material_change_reason(previous: dict[str, Any], current: dict[str, Any]) -> str | None:
    for key in ("signal", "risk_level", "market_state", "sentiment_status", "trade_permission"):
        if previous.get(key) != current.get(key):
            return f"{key}_changed"

    previous_recs = previous.get("recommendations") or []
    current_recs = current.get("recommendations") or []
    if _recommendation_identity(previous_recs) != _recommendation_identity(current_recs):
        return "recommendations_changed"

    if previous.get("no_recommendation_reason") != current.get("no_recommendation_reason"):
        return "no_recommendation_reason_changed"

    if previous.get("watchlist_symbols") != current.get("watchlist_symbols") and not current_recs:
        return "watchlist_changed"

    if _price_changed(previous_recs, current_recs):
        return "price_band_changed"

    return None


def _recommendation_identity(items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(str(item.get("symbol") or ""), str(item.get("action") or "")) for item in items]


def _price_changed(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> bool:
    if len(previous) != len(current):
        return True
    by_symbol = {item.get("symbol"): item for item in previous}
    for item in current:
        old = by_symbol.get(item.get("symbol"))
        if not old:
            return True
        for key in ("buy_range_low", "buy_range_high", "max_buy_price", "stop_loss"):
            change = _relative_change(old.get(key), item.get(key))
            if settings.push_price_change_threshold <= 0 and change > 0:
                return True
            if settings.push_price_change_threshold > 0 and change >= settings.push_price_change_threshold:
                return True
    return False


def _summary_interval_reached(last_sent_at: Any) -> bool:
    if not last_sent_at:
        return True
    try:
        previous = datetime.fromisoformat(str(last_sent_at))
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    elapsed = (datetime.now(timezone.utc) - previous.astimezone(timezone.utc)).total_seconds()
    return elapsed >= settings.push_dedup_summary_minutes * 60


def _heartbeat_interval_reached(last_sent_at: Any) -> bool:
    if not last_sent_at:
        return True
    try:
        previous = datetime.fromisoformat(str(last_sent_at))
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=BEIJING_TZ)
    except ValueError:
        return True
    elapsed = (datetime.now(BEIJING_TZ) - previous.astimezone(BEIJING_TZ)).total_seconds()
    return elapsed >= settings.push_heartbeat_interval_minutes * 60


def _fingerprint(summary: dict[str, Any]) -> str:
    stable = json.dumps(summary, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _loads(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _round_float(value: Any, digits: int) -> float:
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return 0.0


def _relative_change(old_value: Any, new_value: Any) -> float:
    old = _round_float(old_value, 6)
    new = _round_float(new_value, 6)
    if old == new:
        return 0.0
    base = max(abs(old), 0.01)
    return abs(new - old) / base


def _heartbeat_phase(final_signal: dict[str, Any]) -> str | None:
    state = final_signal.get("state") if isinstance(final_signal.get("state"), dict) else {}
    phase = str(state.get("phase") or "").lower()
    if phase == "morning":
        return "morning"
    if phase == "afternoon":
        return "afternoon"
    return None
