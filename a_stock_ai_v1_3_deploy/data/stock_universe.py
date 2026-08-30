from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from app.config import settings


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger(__name__)

_DYNAMIC_PROFILES = {"large", "active_mid", "blended", "institutional", "adaptive"}
_STATIC_PROFILES = {"watchlist", "settings", "current", "default"}


@dataclass(frozen=True)
class StockUniverseSnapshot:
    symbols: list[str]
    tags: dict[str, list[str]]
    profile: str
    limit: int
    as_of_date: str
    source: str
    cache_state: str
    updated_at: str
    fallback_reason: str = ""

    @property
    def fingerprint(self) -> str:
        value = ",".join(self.symbols).encode("utf-8")
        return hashlib.sha256(value).hexdigest()[:16]


def build_stock_universe(
    frame: pd.DataFrame,
    profile_name: str,
    limit: int,
    core_symbols: list[str],
    normalize_symbol: Callable[[str], str] | None = None,
) -> tuple[list[str], dict[str, list[str]]]:
    """Build the shared stock universe used by historical and intraday Hybrid Alpha."""
    profile = str(profile_name or "watchlist").strip().lower()
    cap = max(int(limit or 1), 1)
    normalize = normalize_symbol or (lambda value: str(value).strip().upper())
    core = _dedupe_symbols(core_symbols, normalize)

    if profile in {"watchlist", "settings", "current"}:
        return core, {symbol: ["core"] for symbol in core}
    if profile == "default":
        return core, {symbol: ["core"] for symbol in core}
    if profile not in _DYNAMIC_PROFILES:
        raise ValueError(f"unsupported stock universe profile: {profile_name}")
    if frame is None or frame.empty:
        raise ValueError("daily_basic snapshot is empty")

    columns = {str(column).strip().lower(): column for column in frame.columns}
    ts_col = columns.get("ts_code")
    turnover_col = columns.get("turnover_rate")
    circ_mv_col = columns.get("circ_mv")
    if ts_col is None or turnover_col is None or circ_mv_col is None:
        raise ValueError("daily_basic snapshot is missing ts_code, turnover_rate, or circ_mv")

    data = frame.copy()
    data["_symbol"] = data[ts_col].astype(str).map(normalize)
    data = data[(data["_symbol"] != "") & (~data["_symbol"].str.endswith(".BJ"))]
    data["_turnover"] = pd.to_numeric(data[turnover_col], errors="coerce").fillna(0.0)
    data["_circ_mv"] = pd.to_numeric(data[circ_mv_col], errors="coerce").fillna(0.0)
    data = data[data["_circ_mv"] > 0]

    large = data[data["_turnover"] >= 0.2].sort_values(
        ["_circ_mv", "_turnover"], ascending=[False, False]
    )
    active = data[
        (data["_turnover"] >= 1.0)
        & (data["_circ_mv"] >= 300000)
        & (data["_circ_mv"] <= 12000000)
    ].copy()
    active["_active_value"] = active["_turnover"] * active["_circ_mv"]
    active = active.sort_values("_active_value", ascending=False)

    if profile == "large":
        symbols = _dedupe_symbols(large["_symbol"].head(cap).tolist(), normalize)
        return symbols, {symbol: ["large"] for symbol in symbols}
    if profile == "active_mid":
        symbols = _dedupe_symbols(active["_symbol"].head(cap).tolist(), normalize)
        return symbols, {symbol: ["active"] for symbol in symbols}

    large_count = max(cap // 2, 1)
    active_count = max(cap - large_count, 1)
    pieces: list[tuple[str, list[str]]] = []
    if profile == "institutional":
        large_count = max(int(cap * 0.4), 1)
        active_count = max(cap - large_count, 1)
    if profile == "adaptive":
        core_count = min(len(core), max(int(cap * 0.3), 1))
        large_count = max(int(cap * 0.35), 1)
        active_count = max(cap - core_count - large_count, 1)
        pieces.append(("core", core[:core_count]))

    pieces.extend(
        [
            ("large", large["_symbol"].head(large_count).tolist()),
            ("active", active["_symbol"].head(active_count).tolist()),
        ]
    )
    tags: dict[str, list[str]] = {}
    ordered: list[str] = []
    for tag, symbols in pieces:
        for symbol in _dedupe_symbols(symbols, normalize):
            if symbol not in tags:
                ordered.append(symbol)
                tags[symbol] = []
            if tag not in tags[symbol]:
                tags[symbol].append(tag)
    selected = ordered[:cap]
    return selected, {symbol: tags.get(symbol, []) for symbol in selected}


class StockUniverseManager:
    """Refresh the daily adaptive universe once, then reuse it during intraday cycles."""

    def __init__(self, client: Any, cache_path: Path | None = None) -> None:
        self.client = client
        self.cache_path = (cache_path or settings.stock_selection_universe_cache_path).resolve()

    def resolve(self) -> StockUniverseSnapshot:
        profile = settings.stock_selection_universe_profile
        limit = settings.stock_selection_universe_limit
        core = self.client.core_selection_symbols()
        now = datetime.now(BEIJING_TZ)

        if profile in _STATIC_PROFILES:
            symbols = self.client._default_symbols() if profile == "default" else core
            return StockUniverseSnapshot(
                symbols=symbols,
                tags={symbol: ["core"] for symbol in symbols},
                profile=profile,
                limit=limit,
                as_of_date=now.strftime("%Y%m%d"),
                source="configured_watchlist",
                cache_state="not_required",
                updated_at=now.isoformat(timespec="seconds"),
            )

        cached = self._load_cache(profile, limit, core)
        if cached and self._is_fresh(cached, now):
            return self._snapshot_from_cache(cached, cache_state="hit")

        try:
            frame, as_of_date = self.client.fetch_daily_basic_snapshot(now.strftime("%Y%m%d"))
            symbols, tags = build_stock_universe(
                frame,
                profile_name=profile,
                limit=limit,
                core_symbols=core,
                normalize_symbol=self.client.normalize_symbol,
            )
            if not symbols:
                raise ValueError("daily_basic universe produced no eligible symbols")
            payload = {
                "profile": profile,
                "limit": limit,
                "core_symbols": core,
                "symbols": symbols,
                "tags": tags,
                "as_of_date": as_of_date,
                "source": "tushare_daily_basic",
                "updated_at": now.isoformat(timespec="seconds"),
            }
            self._write_cache(payload)
            return self._snapshot_from_cache(payload, cache_state="refreshed")
        except Exception as exc:
            reason = str(exc)
            logger.warning("adaptive stock universe refresh failed: %s", reason, exc_info=True)
            if cached:
                return self._snapshot_from_cache(
                    cached,
                    cache_state="stale_fallback",
                    fallback_reason=f"daily_basic refresh failed: {reason}",
                )
            fallback = list(core)
            return StockUniverseSnapshot(
                symbols=fallback,
                tags={symbol: ["core"] for symbol in fallback},
                profile=profile,
                limit=limit,
                as_of_date=now.strftime("%Y%m%d"),
                source="core_fallback",
                cache_state="cold_start_fallback",
                updated_at=now.isoformat(timespec="seconds"),
                fallback_reason=f"daily_basic refresh failed: {reason}",
            )

    def _load_cache(self, profile: str, limit: int, core: list[str]) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("profile") != profile or int(payload.get("limit") or 0) != limit:
            return None
        if list(payload.get("core_symbols") or []) != core:
            return None
        if not isinstance(payload.get("symbols"), list) or not payload.get("symbols"):
            return None
        return payload

    def _is_fresh(self, payload: dict[str, Any], now: datetime) -> bool:
        try:
            updated_at = datetime.fromisoformat(str(payload.get("updated_at") or ""))
        except ValueError:
            return False
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=BEIJING_TZ)
        age_seconds = (now - updated_at.astimezone(BEIJING_TZ)).total_seconds()
        return 0 <= age_seconds <= settings.stock_selection_universe_cache_hours * 3600

    def _snapshot_from_cache(
        self,
        payload: dict[str, Any],
        cache_state: str,
        fallback_reason: str = "",
    ) -> StockUniverseSnapshot:
        symbols = _dedupe_symbols(
            [str(symbol) for symbol in payload.get("symbols") or []], self.client.normalize_symbol
        )
        raw_tags = payload.get("tags") if isinstance(payload.get("tags"), dict) else {}
        tags = {
            symbol: [str(tag) for tag in raw_tags.get(symbol, [])]
            for symbol in symbols
        }
        return StockUniverseSnapshot(
            symbols=symbols,
            tags=tags,
            profile=str(payload.get("profile") or settings.stock_selection_universe_profile),
            limit=int(payload.get("limit") or settings.stock_selection_universe_limit),
            as_of_date=str(payload.get("as_of_date") or ""),
            source=str(payload.get("source") or "tushare_daily_basic"),
            cache_state=cache_state,
            updated_at=str(payload.get("updated_at") or ""),
            fallback_reason=fallback_reason,
        )

    def _write_cache(self, payload: dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(f"{self.cache_path.suffix}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.cache_path)


def _dedupe_symbols(symbols: list[Any], normalize_symbol: Callable[[str], str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        value = normalize_symbol(str(symbol))
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
