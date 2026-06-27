from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def to_json(value: Any) -> str:
    def default(item: Any) -> Any:
        if is_dataclass(item):
            return asdict(item)
        if isinstance(item, datetime):
            return item.isoformat()
        return str(item)

    return json.dumps(value, ensure_ascii=False, default=default)


def normalize_symbol(symbol: str) -> str:
    value = str(symbol).strip().upper()
    if not value:
        return value
    if "." in value:
        code, exchange = value.split(".", 1)
        return f"{code.zfill(6)}.{exchange}"
    code = value.zfill(6)
    if code.startswith(("60", "68", "90")):
        return f"{code}.SH"
    if code.startswith(("43", "83", "87", "92")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def symbol_without_exchange(symbol: str) -> str:
    return normalize_symbol(symbol).split(".", 1)[0]

