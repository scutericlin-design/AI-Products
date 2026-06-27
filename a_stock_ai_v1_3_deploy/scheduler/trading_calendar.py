from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
import os
from zoneinfo import ZoneInfo


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
MORNING_OPEN = time(9, 30)
MORNING_CLOSE = time(11, 30)
AFTERNOON_OPEN = time(13, 0)
AFTERNOON_CLOSE = time(15, 0)


@dataclass(frozen=True)
class TradingWindow:
    is_open: bool
    phase: str
    reason: str
    checked_at: str


def current_trading_window(now: datetime | None = None) -> TradingWindow:
    value = now.astimezone(BEIJING_TZ) if now else datetime.now(BEIJING_TZ)
    checked_at = value.isoformat(timespec="seconds")
    trade_date = value.date().isoformat()
    holidays = _csv_dates("A_SHARE_HOLIDAYS")
    extra_trading_days = _csv_dates("A_SHARE_EXTRA_TRADING_DAYS")
    if trade_date in holidays:
        return TradingWindow(False, "closed", "configured_market_holiday", checked_at)
    if value.weekday() >= 5 and trade_date not in extra_trading_days:
        return TradingWindow(False, "closed", "weekend", checked_at)

    current = value.time()
    if MORNING_OPEN <= current <= MORNING_CLOSE:
        return TradingWindow(True, "morning", "trading_session", checked_at)
    if AFTERNOON_OPEN <= current <= AFTERNOON_CLOSE:
        return TradingWindow(True, "afternoon", "trading_session", checked_at)
    if MORNING_CLOSE < current < AFTERNOON_OPEN:
        return TradingWindow(False, "lunch_break", "midday_break", checked_at)
    return TradingWindow(False, "closed", "outside_trading_hours", checked_at)


def is_trading_session(now: datetime | None = None) -> bool:
    return current_trading_window(now).is_open


def is_trading_day(now: datetime | None = None) -> bool:
    value = now.astimezone(BEIJING_TZ) if now else datetime.now(BEIJING_TZ)
    trade_date = value.date().isoformat()
    holidays = _csv_dates("A_SHARE_HOLIDAYS")
    extra_trading_days = _csv_dates("A_SHARE_EXTRA_TRADING_DAYS")
    if trade_date in holidays:
        return False
    if value.weekday() >= 5 and trade_date not in extra_trading_days:
        return False
    return True


def _csv_dates(name: str) -> set[str]:
    return {item.strip() for item in os.getenv(name, "").split(",") if item.strip()}
