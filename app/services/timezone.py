from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc


def now_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def now_beijing_iso(timespec: str = "seconds") -> str:
    return now_beijing().isoformat(timespec=timespec)


def as_beijing(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(BEIJING_TZ)


def beijing_iso(value: datetime | None, timespec: str = "seconds") -> str | None:
    converted = as_beijing(value)
    return converted.isoformat(timespec=timespec) if converted else None


def file_mtime_beijing_iso(path: Path, timespec: str = "seconds") -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=BEIJING_TZ).isoformat(timespec=timespec)
