from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

from app.config import PROJECT_ROOT


LOCAL_NAME_SOURCES = [
    PROJECT_ROOT / "data" / "processed" / "recommended_pool.csv",
    PROJECT_ROOT / "data" / "processed" / "signal_latest.csv",
    PROJECT_ROOT / "data" / "processed" / "signal_daily.csv",
    PROJECT_ROOT / "data" / "portfolio.csv",
]


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().split(".")[0].zfill(6)


def _read_local_name(path: Path, symbol: str) -> str | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row_symbol = normalize_symbol(row.get("symbol", ""))
            name = (row.get("name") or "").strip()
            if row_symbol == symbol and name:
                return name
    return None


@lru_cache(maxsize=1)
def _akshare_code_names() -> dict[str, str]:
    import akshare as ak

    df = ak.stock_info_a_code_name()
    result: dict[str, str] = {}
    for _, row in df.iterrows():
        code = normalize_symbol(str(row.get("code", "")))
        name = str(row.get("name", "")).strip()
        if code and name:
            result[code] = name
    return result


def lookup_stock_name(symbol: str) -> tuple[str, str]:
    normalized = normalize_symbol(symbol)
    for path in LOCAL_NAME_SOURCES:
        name = _read_local_name(path, normalized)
        if name:
            return name, path.name

    try:
        name = _akshare_code_names().get(normalized)
    except Exception:
        name = None

    if name:
        return name, "akshare"
    return normalized, "symbol_fallback"
