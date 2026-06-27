from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from app.config import settings
from data.tushare_client import TushareClient
from scheduler.trading_calendar import BEIJING_TZ
from storage.logger import fetch_buy_decisions


logger = logging.getLogger(__name__)


def evaluate_recent_outcomes(lookback_days: int | None = None) -> dict[str, Any]:
    days = lookback_days or settings.self_learning_lookback_days
    decisions = fetch_buy_decisions(days)
    client = TushareClient()
    cache: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    outcomes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for row in decisions:
        payload = _loads(row.get("payload_json"))
        symbol = str(row.get("symbol") or payload.get("symbol") or "")
        created_at = str(row.get("created_at") or "")
        entry_price = _float(payload.get("current_price") or payload.get("price"), 0.0)
        if not symbol or not created_at or entry_price <= 0:
            skipped.append({"symbol": symbol, "reason": "missing_symbol_or_entry_price"})
            continue

        start_date, end_date = _evaluation_window(created_at)
        key = (symbol, start_date, end_date)
        if key not in cache:
            try:
                cache[key] = client.fetch_daily_bars(symbol, start_date, end_date)
            except Exception as exc:
                logger.warning("daily bar fetch failed for %s: %s", symbol, exc)
                cache[key] = []
        bars = cache[key]
        if not bars:
            skipped.append({"symbol": symbol, "reason": "no_daily_bar", "date": start_date})
            continue

        outcome = _build_outcome(row, payload, bars, entry_price)
        if outcome:
            outcomes.append(outcome)
        else:
            skipped.append({"symbol": symbol, "reason": "invalid_daily_bar", "date": start_date})

    return {
        "lookback_days": days,
        "sample_count": len(decisions),
        "evaluated_count": len(outcomes),
        "skipped_count": len(skipped),
        "outcomes": outcomes,
        "skipped": skipped[:20],
    }


class OutcomeEvaluator:
    def evaluate(self, lookback_days: int | None = None) -> dict[str, Any]:
        return evaluate_recent_outcomes(lookback_days)


def _build_outcome(
    row: dict[str, Any],
    payload: dict[str, Any],
    bars: list[dict[str, Any]],
    entry_price: float,
) -> dict[str, Any] | None:
    usable = [bar for bar in bars if _float(bar.get("close"), 0.0) > 0]
    if not usable:
        return None
    first_bar = usable[0]
    close_price = _float(first_bar.get("close"), 0.0)
    high_price = _float(first_bar.get("high"), close_price)
    low_price = _float(first_bar.get("low"), close_price)
    stop_loss = _float(payload.get("stop_loss"), 0.0)
    close_return_pct = (close_price / entry_price - 1) * 100
    high_return_pct = (high_price / entry_price - 1) * 100
    low_return_pct = (low_price / entry_price - 1) * 100
    stop_hit = bool(stop_loss > 0 and low_price <= stop_loss)
    outcome_score = close_return_pct + high_return_pct * 0.35 + min(low_return_pct, 0) * 0.65
    if stop_hit:
        outcome_score -= 2.0

    return {
        "cycle_id": row.get("cycle_id"),
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "created_at": row.get("created_at"),
        "trade_date": first_bar.get("trade_date"),
        "entry_price": round(entry_price, 4),
        "close_price": round(close_price, 4),
        "high_price": round(high_price, 4),
        "low_price": round(low_price, 4),
        "close_return_pct": round(close_return_pct, 4),
        "max_favorable_pct": round(high_return_pct, 4),
        "max_adverse_pct": round(low_return_pct, 4),
        "stop_hit": stop_hit,
        "success": outcome_score > 0 and close_return_pct > -1.0,
        "outcome_score": round(outcome_score, 4),
        "features": {
            "rank_score": _float(payload.get("rank_score"), 0.0),
            "confidence": _float(payload.get("confidence"), 0.0),
            "pct_change": _float(payload.get("pct_change"), 0.0),
            "amount_yi": _float(payload.get("amount_yi"), 0.0),
            "leader_strength": _float(payload.get("leader_strength"), 0.0),
            "quality_score": _float(payload.get("quality_score"), 0.0),
            "tradability_score": _float(payload.get("tradability_score"), 0.0),
            "sentiment_score": _float(payload.get("sentiment_score"), 0.0),
            "trade_permission": str(payload.get("trade_permission") or ""),
        },
    }


def _evaluation_window(created_at: str) -> tuple[str, str]:
    created = datetime.fromisoformat(created_at)
    if created.tzinfo is None:
        created = created.replace(tzinfo=BEIJING_TZ)
    start = created.astimezone(BEIJING_TZ)
    end = min(datetime.now(BEIJING_TZ), start + timedelta(days=5))
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _loads(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
