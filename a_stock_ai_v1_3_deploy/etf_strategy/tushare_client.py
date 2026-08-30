from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import pandas as pd

from etf_strategy.config import ETFStrategySettings
from etf_strategy.models import ETFBar, ETFInstrument


logger = logging.getLogger(__name__)


class ETFDataError(RuntimeError):
    pass


class TuShareETFClient:
    """TuShare ETF adapter with support for the configured proxy endpoint.

    ETF endpoints vary by account entitlement. The client first uses ETF-specific
    endpoints and then falls back to the older fund endpoints where compatible.
    It never fabricates data when an endpoint is unavailable.
    """

    def __init__(self, settings: ETFStrategySettings):
        self.settings = settings
        self._pro: Any | None = None

    def fetch_etf_universe(self) -> list[ETFInstrument]:
        frames: list[pd.DataFrame] = []
        errors: list[str] = []
        for status in ("L", "D", "P"):
            try:
                frame, _ = self._call_first(
                    ("etf_basic",),
                    list_status=status,
                    fields=(
                        "ts_code,csname,extname,cname,index_code,index_name,exchange,"
                        "etf_type,list_date,delist_date,list_status"
                    ),
                )
                if frame is not None and not frame.empty:
                    frames.append(frame)
            except Exception as exc:
                errors.append(f"etf_basic[{status}]: {exc}")

        if not frames:
            try:
                frame, _ = self._call_first(
                    ("fund_basic",),
                    market="E",
                    fields="ts_code,name,management,custodian,fund_type,found_date,due_date,list_date,delist_date,status",
                )
                if frame is not None and not frame.empty:
                    frames.append(frame)
            except Exception as exc:
                errors.append(f"fund_basic: {exc}")

        if not frames:
            detail = " | ".join(errors) or "no ETF universe returned"
            raise ETFDataError(f"TuShare ETF universe unavailable: {detail}")

        frame = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts_code"], keep="first")
        columns = _columns(frame)
        instruments: list[ETFInstrument] = []
        for _, row in frame.iterrows():
            symbol = _text(row, columns, "ts_code")
            if not symbol:
                continue
            instruments.append(
                ETFInstrument(
                    symbol=_symbol(symbol),
                    name=(
                        _text(row, columns, "csname", "extname", "name", "cname")
                        or _symbol(symbol)
                    ),
                    index_code=_text(row, columns, "index_code"),
                    index_name=_text(row, columns, "index_name"),
                    exchange=_text(row, columns, "exchange"),
                    etf_type=_text(row, columns, "etf_type", "fund_type"),
                    list_date=_date_text(_text(row, columns, "list_date", "found_date")),
                    delist_date=_date_text(_text(row, columns, "delist_date", "due_date")),
                    list_status=_text(row, columns, "list_status", "status") or "L",
                )
            )
        return instruments

    def fetch_open_trade_dates(self, start_date: str, end_date: str) -> list[str]:
        try:
            frame, _ = self._call_first(
                ("trade_cal",),
                exchange="SSE",
                start_date=start_date,
                end_date=end_date,
                is_open="1",
                fields="cal_date,is_open",
            )
            if frame is not None and not frame.empty:
                columns = _columns(frame)
                dates = [_date_text(_text(row, columns, "cal_date", "trade_date")) for _, row in frame.iterrows()]
                return sorted(item for item in dates if item)
        except Exception as exc:
            logger.warning("TuShare trade calendar unavailable; using weekday candidates: %s", exc)

        start = datetime.strptime(start_date, "%Y%m%d").date()
        end = datetime.strptime(end_date, "%Y%m%d").date()
        candidates: list[str] = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                candidates.append(current.strftime("%Y%m%d"))
            current += timedelta(days=1)
        return candidates

    def fetch_daily_snapshot(self, trade_date: str) -> tuple[list[ETFBar], str]:
        frame, endpoint = self._call_first(
            ("etf_daily", "fund_daily"),
            skip_empty=True,
            trade_date=trade_date,
        )
        if frame is None or frame.empty:
            return [], endpoint
        columns = _columns(frame)
        bars: list[ETFBar] = []
        for _, row in frame.iterrows():
            symbol = _symbol(_text(row, columns, "ts_code", "code"))
            bar_date = _date_text(_text(row, columns, "trade_date", "date")) or trade_date
            close = _number(row, columns, "close")
            if not symbol or close <= 0:
                continue
            bars.append(
                ETFBar(
                    symbol=symbol,
                    trade_date=bar_date,
                    open=_number(row, columns, "open"),
                    high=_number(row, columns, "high"),
                    low=_number(row, columns, "low"),
                    close=close,
                    pre_close=_number(row, columns, "pre_close"),
                    pct_chg=_number(row, columns, "pct_chg", "pct_change"),
                    volume=_number(row, columns, "vol", "volume"),
                    amount=_number(row, columns, "amount"),
                    source=f"tushare:{endpoint}",
                )
            )
        return bars, endpoint

    def fetch_index_daily(self, symbol: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        frame, _ = self._call_first(
            ("index_daily",),
            ts_code=symbol,
            start_date=start_date,
            end_date=end_date,
        )
        if frame is None or frame.empty:
            return []
        columns = _columns(frame)
        rows: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            trade_date = _date_text(_text(row, columns, "trade_date", "date"))
            close = _number(row, columns, "close")
            if not trade_date or close <= 0:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "open": _number(row, columns, "open"),
                    "high": _number(row, columns, "high"),
                    "low": _number(row, columns, "low"),
                    "close": close,
                    "pre_close": _number(row, columns, "pre_close"),
                    "pct_chg": _number(row, columns, "pct_chg", "pct_change"),
                    "amount": _number(row, columns, "amount"),
                }
            )
        return rows

    def fetch_nav(self, symbol: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        frame, _ = self._call_first(
            ("fund_nav",),
            ts_code=symbol,
            market="E",
            start_date=start_date,
            end_date=end_date,
        )
        if frame is None or frame.empty:
            return []
        columns = _columns(frame)
        rows: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            nav_date = _date_text(_text(row, columns, "nav_date", "trade_date"))
            nav = _number(row, columns, "unit_nav", "adj_nav")
            if nav_date and nav > 0:
                rows.append(
                    {
                        "symbol": symbol,
                        "nav_date": nav_date,
                        "unit_nav": nav,
                        "accum_nav": _number(row, columns, "accum_nav"),
                        "ann_date": _date_text(_text(row, columns, "ann_date")),
                    }
                )
        return rows

    def fetch_today_minutes(self, symbol: str) -> pd.DataFrame:
        try:
            frame, _ = self._call_first(("rt_etf_min_daily",), ts_code=symbol, freq="1MIN")
            result = self._realtime_minute_frame(frame, source="tushare:rt_etf_min_daily")
            if not result.empty:
                return result
            logger.warning("TuShare ETF realtime minutes returned no rows for %s", symbol)
        except ETFDataError as exc:
            logger.warning("TuShare ETF realtime minutes unavailable for %s: %s", symbol, exc)

        try:
            result = self._fetch_today_minutes_from_tushare_history(symbol)
            if not result.empty:
                return result
            logger.warning("TuShare ETF historical minute fallback returned no rows for %s", symbol)
        except ETFDataError as exc:
            logger.warning("TuShare ETF historical minute fallback unavailable for %s: %s", symbol, exc)

        if not self.settings.minute_akshare_fallback_enabled:
            return pd.DataFrame()
        try:
            return self._fetch_today_minutes_akshare(symbol)
        except Exception as exc:
            logger.warning("AKShare ETF realtime minute fallback unavailable for %s: %s", symbol, exc)
            return pd.DataFrame()

    def fetch_today_minutes_fast(self, symbol: str) -> pd.DataFrame:
        """Prefer one direct ETF minute request before the slower TuShare chain.

        The static-pool source strategy uses this only after it has already
        selected one target. It prevents a transient TuShare proxy timeout from
        consuming an entire scheduled minute before the lightweight backup runs.
        """
        if self.settings.minute_akshare_fallback_enabled:
            try:
                result = self._fetch_today_minutes_akshare(symbol)
                if not result.empty:
                    return result
            except Exception as exc:
                logger.warning("AKShare fast ETF minute unavailable for %s: %s", symbol, exc)
        return self.fetch_today_minutes(symbol)

    def fetch_realtime_spot_minutes(self, symbols: set[str] | frozenset[str], captured_at: datetime) -> dict[str, pd.DataFrame]:
        """Fetch a single batch ETF spot snapshot and expose it as minute bars.

        Eastmoney's ETF board is paginated internally by AKShare but covers the
        complete static pool in one coordinated fetch. The snapshot timestamp is
        floored to the minute so the source-file 13:08 rule can consume it
        deterministically even when the HTTP response returns seconds later.
        """
        if not self.settings.minute_batch_spot_enabled or not symbols:
            return {}
        try:
            import akshare as ak

            frame = ak.fund_etf_spot_em()
        except Exception as exc:
            raise ETFDataError(f"AKShare ETF batch spot unavailable: {exc}") from exc
        if frame is None or frame.empty:
            raise ETFDataError("AKShare ETF batch spot returned no rows")

        wanted = {_symbol(symbol) for symbol in symbols}
        columns = _columns(frame)
        snapshot_time = captured_at.replace(second=0, microsecond=0, tzinfo=None)
        rows: dict[str, pd.DataFrame] = {}
        for _, row in frame.iterrows():
            symbol = _symbol(_text(row, columns, "代码", "code", "symbol"))
            if symbol not in wanted:
                continue
            price = _number(row, columns, "最新价", "price", "close")
            if price <= 0:
                continue
            minute = pd.DataFrame(
                [
                    {
                        "trade_time": snapshot_time,
                        "open": _number(row, columns, "开盘价", "open") or price,
                        "high": _number(row, columns, "最高价", "high") or price,
                        "low": _number(row, columns, "最低价", "low") or price,
                        "close": price,
                        "vol": _number(row, columns, "成交量", "vol", "volume"),
                        "amount": _number(row, columns, "成交额", "amount"),
                    }
                ]
            )
            minute.attrs["source"] = "akshare:fund_etf_spot_em"
            rows[symbol] = minute
        return rows

    def fetch_realtime_spot_daily(self, symbols: set[str] | frozenset[str], trade_date: str) -> list[ETFBar]:
        """Use the batch ETF board to fill a missing prior-close static snapshot.

        This is only a continuity fallback for a failed TuShare daily refresh;
        it does not replace normal historical daily data or expand the pool.
        """
        if not self.settings.minute_batch_spot_enabled or not symbols:
            return []
        try:
            import akshare as ak

            frame = ak.fund_etf_spot_em()
        except Exception as exc:
            raise ETFDataError(f"AKShare ETF batch daily fallback unavailable: {exc}") from exc
        if frame is None or frame.empty:
            return []

        wanted = {_symbol(symbol) for symbol in symbols}
        columns = _columns(frame)
        bars: list[ETFBar] = []
        for _, row in frame.iterrows():
            symbol = _symbol(_text(row, columns, "代码", "code", "symbol"))
            if symbol not in wanted:
                continue
            close = _number(row, columns, "最新价", "price", "close")
            if close <= 0:
                continue
            bars.append(
                ETFBar(
                    symbol=symbol,
                    trade_date=trade_date,
                    open=_number(row, columns, "开盘价", "open") or close,
                    high=_number(row, columns, "最高价", "high") or close,
                    low=_number(row, columns, "最低价", "low") or close,
                    close=close,
                    pre_close=_number(row, columns, "昨收", "pre_close"),
                    pct_chg=_number(row, columns, "涨跌幅", "pct_chg", "pct_change"),
                    volume=_number(row, columns, "成交量", "vol", "volume"),
                    amount=_number(row, columns, "成交额", "amount"),
                    source="akshare:fund_etf_spot_em_prior_close",
                )
            )
        return bars

    def fetch_daily_history_akshare(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[ETFBar]:
        """Load a bounded ETF daily-history window from AKShare.

        This is a recovery path for the fixed source-file pool when the TuShare
        proxy cannot provide whole-market daily snapshots. It is deliberately
        bounded to the strategy warm-up window and only called for symbols that
        do not already have enough local history.
        """
        try:
            import akshare as ak

            frame = ak.fund_etf_hist_em(
                symbol=_symbol(symbol).split(".", 1)[0],
                period="daily",
                start_date=_compact_date(start_date),
                end_date=_compact_date(end_date),
                adjust="",
            )
        except Exception as exc:
            raise ETFDataError(f"AKShare ETF daily history unavailable for {symbol}: {exc}") from exc
        if frame is None or frame.empty:
            return []

        normalized_symbol = _symbol(symbol)
        columns = _columns(frame)
        result: list[ETFBar] = []
        prior_close = 0.0
        for _, row in frame.iterrows():
            trade_date = _date_text(_text(row, columns, "日期", "date", "trade_date"))
            close = _number(row, columns, "收盘", "close")
            if not trade_date or close <= 0:
                continue
            change = _number(row, columns, "涨跌额", "change")
            pct_chg = _number(row, columns, "涨跌幅", "pct_chg", "pct_change")
            pre_close = close - change if close - change > 0 else prior_close
            if pre_close <= 0 and pct_chg > -100:
                pre_close = close / (1 + pct_chg / 100)
            result.append(
                ETFBar(
                    symbol=normalized_symbol,
                    trade_date=trade_date,
                    open=_number(row, columns, "开盘", "open") or close,
                    high=_number(row, columns, "最高", "high") or close,
                    low=_number(row, columns, "最低", "low") or close,
                    close=close,
                    pre_close=pre_close,
                    pct_chg=pct_chg,
                    volume=_number(row, columns, "成交量", "vol", "volume"),
                    amount=_number(row, columns, "成交额", "amount"),
                    source="akshare:fund_etf_hist_em",
                )
            )
            prior_close = close
        return result

    def fetch_daily_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[ETFBar]:
        """Fetch one ETF's bounded daily history with TuShare first.

        The configured proxy reliably serves a single fund's history but may
        time out for whole-market, per-day snapshots. Keeping this request
        symbol-scoped is the stable primary path; AKShare remains a fallback.
        """
        normalized_symbol = _symbol(symbol)
        try:
            frame, endpoint = self._call_first(
                ("etf_daily", "fund_daily"),
                skip_empty=True,
                ts_code=normalized_symbol,
                start_date=_compact_date(start_date),
                end_date=_compact_date(end_date),
            )
            bars = self._daily_bars_from_frame(frame, endpoint, normalized_symbol)
            if bars:
                return bars
        except ETFDataError as exc:
            logger.warning("TuShare ETF daily history unavailable for %s: %s", normalized_symbol, exc)
        return self.fetch_daily_history_akshare(normalized_symbol, start_date, end_date)

    def fetch_index_daily_akshare(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Load a bounded A-share index daily window when the proxy is down."""
        normalized_symbol = str(symbol or "").upper()
        exchange = "sh" if normalized_symbol.endswith(".SH") else "sz"
        code = normalized_symbol.split(".", 1)[0]
        try:
            import akshare as ak

            frame = ak.stock_zh_index_daily(symbol=f"{exchange}{code}")
        except Exception as exc:
            raise ETFDataError(f"AKShare index daily history unavailable for {symbol}: {exc}") from exc
        if frame is None or frame.empty:
            return []

        start = _compact_date(start_date)
        end = _compact_date(end_date)
        columns = _columns(frame)
        result: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            trade_date = _date_text(_text(row, columns, "date", "日期", "trade_date"))
            close = _number(row, columns, "close", "收盘")
            if not trade_date or close <= 0 or trade_date < start or trade_date > end:
                continue
            result.append(
                {
                    "symbol": normalized_symbol,
                    "trade_date": trade_date,
                    "open": _number(row, columns, "open", "开盘"),
                    "high": _number(row, columns, "high", "最高"),
                    "low": _number(row, columns, "low", "最低"),
                    "close": close,
                    "pre_close": _number(row, columns, "pre_close", "昨收"),
                    "pct_chg": _number(row, columns, "pct_chg", "pct_change", "涨跌幅"),
                    "amount": _number(row, columns, "amount", "成交额"),
                }
            )
        return result

    def fetch_historical_minutes(
        self,
        symbol: str,
        start_time: str,
        end_time: str,
        freq: str = "1min",
    ) -> pd.DataFrame:
        """Load historical ETF bars for a single symbol and bounded time range."""
        frame, _ = self._call_first(
            ("etf_mins",),
            ts_code=_symbol(symbol),
            freq=freq,
            start_date=start_time,
            end_date=end_time,
        )
        if frame is None or frame.empty:
            return pd.DataFrame(columns=["trade_time", "open", "high", "low", "close", "vol", "amount"])
        columns = _columns(frame)
        result = pd.DataFrame(
            {
                "trade_time": [_text(row, columns, "trade_time", "time") for _, row in frame.iterrows()],
                "open": [_number(row, columns, "open") for _, row in frame.iterrows()],
                "high": [_number(row, columns, "high") for _, row in frame.iterrows()],
                "low": [_number(row, columns, "low") for _, row in frame.iterrows()],
                "close": [_number(row, columns, "close") for _, row in frame.iterrows()],
                "vol": [_number(row, columns, "vol", "volume") for _, row in frame.iterrows()],
                "amount": [_number(row, columns, "amount") for _, row in frame.iterrows()],
            }
        )
        result = result[result["close"] > 0].copy()
        result["trade_time"] = pd.to_datetime(result["trade_time"], errors="coerce")
        return result.dropna(subset=["trade_time"]).sort_values("trade_time").reset_index(drop=True)

    def _call_first(
        self,
        endpoints: Iterable[str],
        *,
        skip_empty: bool = False,
        **kwargs: Any,
    ) -> tuple[pd.DataFrame, str]:
        errors: list[str] = []
        for endpoint in endpoints:
            try:
                frame = getattr(self._pro_api(), endpoint)(**kwargs)
                self._pause()
                if isinstance(frame, pd.DataFrame) and (not skip_empty or not frame.empty):
                    return frame, endpoint
                errors.append(f"{endpoint}: {'empty response' if isinstance(frame, pd.DataFrame) else 'non-dataframe response'}")
            except Exception as exc:
                errors.append(f"{endpoint}: {exc}")
        raise ETFDataError(" | ".join(errors))

    def _pro_api(self) -> Any:
        if self._pro is not None:
            return self._pro
        if not self.settings.tushare_token:
            raise ETFDataError("Missing TUSHARE_TOKEN or ETF_TUSHARE_TOKEN for local ETF data refresh.")
        import tushare as ts

        ts.set_token(self.settings.tushare_token)
        pro = ts.pro_api()
        setattr(pro, "_DataApi__token", self.settings.tushare_token)
        setattr(pro, "_DataApi_token", self.settings.tushare_token)
        if self.settings.tushare_base_url:
            setattr(pro, "_DataApi__http_url", self.settings.tushare_base_url.rstrip("/"))
        self._pro = pro
        return pro

    def _pause(self) -> None:
        if self.settings.request_pause_seconds:
            time.sleep(self.settings.request_pause_seconds)

    def _fetch_today_minutes_akshare(self, symbol: str) -> pd.DataFrame:
        """Use Eastmoney ETF minutes only when the TuShare realtime endpoint is empty."""
        import akshare as ak

        now = datetime.now()
        frame = ak.fund_etf_hist_min_em(
            symbol=_symbol(symbol).split(".", 1)[0],
            period="1",
            start_date=now.strftime("%Y-%m-%d 09:30:00"),
            end_date=now.strftime("%Y-%m-%d %H:%M:%S"),
            adjust="",
        )
        return self._realtime_minute_frame(frame, source="akshare:fund_etf_hist_min_em")

    def _fetch_today_minutes_from_tushare_history(self, symbol: str) -> pd.DataFrame:
        now = datetime.now()
        day = now.strftime("%Y-%m-%d")
        frame = self.fetch_historical_minutes(
            symbol,
            f"{day} 09:30:00",
            now.strftime("%Y-%m-%d %H:%M:%S"),
            freq="1min",
        )
        return self._realtime_minute_frame(frame, source="tushare:etf_mins_current_day")

    def _realtime_minute_frame(self, frame: pd.DataFrame | None, source: str) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame()
        columns = _columns(frame)
        result = pd.DataFrame(
            {
                "time": [_text(row, columns, "time", "trade_time", "datetime", "时间") for _, row in frame.iterrows()],
                "open": [_number(row, columns, "open", "开盘") for _, row in frame.iterrows()],
                "high": [_number(row, columns, "high", "最高") for _, row in frame.iterrows()],
                "low": [_number(row, columns, "low", "最低") for _, row in frame.iterrows()],
                "close": [_number(row, columns, "close", "price", "收盘") for _, row in frame.iterrows()],
                "vol": [_number(row, columns, "vol", "volume", "成交量") for _, row in frame.iterrows()],
                "amount": [_number(row, columns, "amount", "成交额") for _, row in frame.iterrows()],
            }
        )
        result = result[result["close"] > 0].reset_index(drop=True)
        result.attrs["source"] = source
        return result

    def _daily_bars_from_frame(self, frame: pd.DataFrame, endpoint: str, symbol: str) -> list[ETFBar]:
        columns = _columns(frame)
        bars: list[ETFBar] = []
        for _, row in frame.iterrows():
            bar_symbol = _symbol(_text(row, columns, "ts_code", "code")) or symbol
            trade_date = _date_text(_text(row, columns, "trade_date", "date"))
            close = _number(row, columns, "close")
            if not trade_date or close <= 0:
                continue
            bars.append(
                ETFBar(
                    symbol=bar_symbol,
                    trade_date=trade_date,
                    open=_number(row, columns, "open") or close,
                    high=_number(row, columns, "high") or close,
                    low=_number(row, columns, "low") or close,
                    close=close,
                    pre_close=_number(row, columns, "pre_close"),
                    pct_chg=_number(row, columns, "pct_chg", "pct_change"),
                    volume=_number(row, columns, "vol", "volume"),
                    amount=_number(row, columns, "amount"),
                    source=f"tushare:{endpoint}",
                )
            )
        return bars


def _columns(frame: pd.DataFrame) -> dict[str, Any]:
    return {str(column).strip().lower(): column for column in frame.columns}


def _text(row: pd.Series, columns: dict[str, Any], *names: str) -> str:
    for name in names:
        column = columns.get(name.lower())
        if column is not None:
            value = row.get(column)
            if value is not None and not pd.isna(value):
                return str(value).strip()
    return ""


def _number(row: pd.Series, columns: dict[str, Any], *names: str) -> float:
    value = _text(row, columns, *names)
    try:
        return float(value) if value else 0.0
    except ValueError:
        return 0.0


def _date_text(value: str) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _compact_date(value: str) -> str:
    compact = _date_text(value)
    if not compact:
        raise ETFDataError(f"Invalid date: {value}")
    return compact


def _symbol(value: str) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol:
        return ""
    if "." in symbol:
        code, exchange = symbol.split(".", 1)
        return f"{code.zfill(6)}.{exchange}"
    if symbol.startswith(("5", "6", "9")):
        return f"{symbol.zfill(6)}.SH"
    return f"{symbol.zfill(6)}.SZ"
