from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import requests
from sqlalchemy import select

from app.config import PROJECT_ROOT, settings
from app.database import SessionLocal
from app.models import DataSourceConfig
from app.security import decrypt_secret
from app.trading.types import Quote
from app.trading.utils import normalize_symbol, parse_csv, safe_float, symbol_without_exchange


FALLBACK_POOL_PATHS = [
    PROJECT_ROOT / "data" / "processed" / "recommended_pool_short.csv",
    PROJECT_ROOT / "data" / "processed" / "recommended_pool.csv",
    PROJECT_ROOT / "data" / "processed" / "scored_universe_latest.csv",
]


class TushareDataLayer:
    def __init__(self, pool_paths: list[Path] | None = None) -> None:
        self.pool_paths = pool_paths or FALLBACK_POOL_PATHS
        self._resolved_tushare_token: str | None = None
        self._tried_database_token = False

    def configured_symbols(self) -> list[str]:
        return [normalize_symbol(item) for item in parse_csv(settings.trading_symbols)]

    def default_symbols(self, limit: int | None = None) -> list[str]:
        frame = self._read_first_pool()
        if frame is None or "symbol" not in frame.columns:
            return []
        symbols = [normalize_symbol(item) for item in frame["symbol"].dropna().astype(str).tolist()]
        return symbols[: limit or settings.trading_max_candidates]

    def fetch_quotes(self, symbols: list[str] | None = None) -> list[Quote]:
        normalized_symbols = [normalize_symbol(item) for item in (symbols or self.configured_symbols())]
        if not normalized_symbols:
            normalized_symbols = self.default_symbols(settings.trading_max_candidates)

        if settings.trading_dry_run:
            return self._fallback_quotes(normalized_symbols)

        if normalized_symbols:
            try:
                quotes = self._fetch_realtime_quotes(normalized_symbols)
                if quotes:
                    return quotes
            except Exception:
                # A provider failure must never turn into an implicit stale
                # quote. Fall through to the independent public quote source.
                pass
            try:
                quotes = self._fetch_eastmoney_quotes(normalized_symbols)
                if quotes:
                    return quotes
            except Exception:
                pass

        return []

    def _fetch_realtime_quotes(self, symbols: list[str]) -> list[Quote]:
        import tushare as ts

        token = self._tushare_token()
        if token:
            ts.set_token(token)

        frame = None
        if hasattr(ts, "realtime_quote"):
            frame = ts.realtime_quote(ts_code=",".join(symbols))
        if frame is None or frame.empty:
            old_symbols = ",".join(symbol_without_exchange(item) for item in symbols)
            frame = ts.get_realtime_quotes(old_symbols)
        if frame is None or frame.empty:
            return []
        return self._quotes_from_frame(frame, source="tushare_realtime")

    def _fetch_eastmoney_quotes(self, symbols: list[str]) -> list[Quote]:
        secids = []
        for symbol in symbols:
            normalized = normalize_symbol(symbol)
            code, exchange = normalized.split(".", 1)
            market = "1" if exchange == "SH" else "0"
            secids.append(f"{market}.{code}")
        response = requests.get(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            params={
                "fltt": "2",
                "invt": "2",
                "fields": "f12,f14,f2,f3,f6,f9",
                "secids": ",".join(secids),
            },
            timeout=12,
        )
        response.raise_for_status()
        rows = ((response.json().get("data") or {}).get("diff") or [])
        quotes: list[Quote] = []
        for row in rows:
            code = str(row.get("f12") or "").zfill(6)
            if not code:
                continue
            symbol = normalize_symbol(code)
            price = safe_float(row.get("f2"))
            if price <= 0:
                continue
            pe = safe_float(row.get("f9"))
            quotes.append(
                Quote(
                    symbol=symbol,
                    name=str(row.get("f14") or code),
                    price=price,
                    pct_change=safe_float(row.get("f3")),
                    amount_yi=safe_float(row.get("f6")) / 100000000,
                    pe_ttm=pe if pe > 0 else None,
                    source="eastmoney_realtime",
                    raw={"source_fields": row},
                )
            )
        return quotes

    def _tushare_token(self) -> str | None:
        """Use an environment token first, then the existing encrypted source.

        The token is never serialized, logged, or returned by this data layer.
        This matches the existing portfolio-refresh behaviour and lets the
        private scheduler share the user's already configured data source.
        """
        if settings.trading_tushare_token:
            return settings.trading_tushare_token
        if self._tried_database_token:
            return self._resolved_tushare_token
        self._tried_database_token = True
        db = SessionLocal()
        try:
            config = db.scalar(
                select(DataSourceConfig)
                .where(
                    DataSourceConfig.provider == "tushare",
                    DataSourceConfig.status.in_({"available", "configured_manual_check"}),
                    DataSourceConfig.api_token_cipher.is_not(None),
                )
                .order_by(DataSourceConfig.priority.asc(), DataSourceConfig.user_id.asc())
            )
            self._resolved_tushare_token = decrypt_secret(config.api_token_cipher) if config else None
            return self._resolved_tushare_token
        finally:
            db.close()

    def _fallback_quotes(self, preferred_symbols: list[str]) -> list[Quote]:
        frame = self._read_first_pool()
        if frame is None or frame.empty:
            return []
        if preferred_symbols and "symbol" in frame.columns:
            preferred = {symbol_without_exchange(item) for item in preferred_symbols}
            frame = frame[frame["symbol"].astype(str).str.zfill(6).isin(preferred)]
        if frame.empty:
            return []
        return self._quotes_from_frame(frame.head(settings.trading_max_candidates), source="local_pool_dry_run")

    def _read_first_pool(self) -> pd.DataFrame | None:
        for path in self.pool_paths:
            if not path.exists() or path.stat().st_size == 0:
                continue
            frame = pd.read_csv(path, encoding="utf-8-sig")
            if not frame.empty:
                return frame
        return None

    def _quotes_from_frame(self, frame: pd.DataFrame, source: str) -> list[Quote]:
        normalized = {str(column).strip().lower(): column for column in frame.columns}
        quotes: list[Quote] = []
        for _, row in frame.iterrows():
            raw = row.to_dict()
            symbol = self._row_value(row, normalized, "ts_code", "symbol", "code")
            if not symbol:
                continue
            name = str(self._row_value(row, normalized, "name", "stock_name") or symbol)
            price = self._number(row, normalized, "price", "close", "latest_close", "trade")
            pct_change = self._number(row, normalized, "pct_change", "pct_chg", "pchange", "changepercent")
            if pct_change == 0:
                pct_change = self._computed_pct_change(row, normalized, price)
            amount = self._number(row, normalized, "amount_yi")
            if amount == 0:
                amount_raw = self._number(row, normalized, "amount")
                amount = amount_raw / 100000000 if amount_raw > 1000000 else amount_raw
            volume_ratio = self._optional_number(row, normalized, "volume_ratio", "vol_ratio")
            pe_ttm = self._optional_number(row, normalized, "pe_ttm", "pe", "pe_ratio", "pettm")
            quotes.append(
                Quote(
                    symbol=normalize_symbol(str(symbol)),
                    name=name,
                    price=price,
                    pct_change=pct_change,
                    amount_yi=amount,
                    volume_ratio=volume_ratio,
                    pe_ttm=pe_ttm if pe_ttm and pe_ttm > 0 else None,
                    source=source,
                    raw={str(key): self._json_safe(value) for key, value in raw.items()},
                )
            )
        return quotes

    def _row_value(self, row: pd.Series, columns: dict[str, Any], *names: str) -> Any:
        for name in names:
            column = columns.get(name)
            if column is not None:
                return row.get(column)
        return None

    def _number(self, row: pd.Series, columns: dict[str, Any], *names: str) -> float:
        return safe_float(self._row_value(row, columns, *names))

    def _optional_number(self, row: pd.Series, columns: dict[str, Any], *names: str) -> float | None:
        value = self._row_value(row, columns, *names)
        if value is None or value == "":
            return None
        return safe_float(value)

    def _computed_pct_change(self, row: pd.Series, columns: dict[str, Any], price: float) -> float:
        previous_close = self._number(row, columns, "pre_close", "prev_close", "settlement")
        if previous_close <= 0 or price <= 0:
            return 0.0
        return round((price - previous_close) / previous_close * 100, 4)

    def _json_safe(self, value: Any) -> Any:
        if pd.isna(value):
            return None
        if hasattr(value, "item"):
            return value.item()
        return value
