from __future__ import annotations

import logging
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import StringIO
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from app.config import settings


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    price: float
    pct_change: float
    amount_yi: float
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    pre_close: float = 0.0
    bid_price: float = 0.0
    ask_price: float = 0.0
    turnover_rate: float = 0.0
    volume_ratio: float = 1.0
    source: str = "tushare"
    timestamp: str = field(default_factory=lambda: datetime.now(BEIJING_TZ).isoformat(timespec="seconds"))
    raw: dict[str, Any] = field(default_factory=dict)


class TushareClient:
    def fetch_realtime_quotes(self, symbols: list[str] | None = None) -> list[Quote]:
        normalized = [self.normalize_symbol(item) for item in (symbols or settings.watch_symbols)]
        if settings.dry_run:
            return self._dry_run_quotes(normalized)
        if not normalized:
            normalized = self._default_symbols()
        try:
            quotes = self._fetch_from_tushare(normalized)
            if quotes:
                return quotes
        except Exception as exc:
            logger.warning("tushare realtime quote fetch failed: %s", exc, exc_info=True)
        if settings.akshare_enabled:
            try:
                quotes = self._fetch_from_akshare(normalized)
                if quotes:
                    return quotes
            except Exception as exc:
                logger.warning("akshare realtime quote fetch failed: %s", exc, exc_info=True)
        return []

    def fetch_daily_bars(self, symbol: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        if settings.dry_run or not symbol:
            return []
        import tushare as ts

        normalized = self.normalize_symbol(symbol)
        if settings.tushare_base_url:
            pro = self._proxy_pro_api(ts)
        else:
            if settings.tushare_token:
                ts.set_token(settings.tushare_token)
            pro = ts.pro_api()
        frame = pro.daily(ts_code=normalized, start_date=start_date, end_date=end_date)
        if frame is None or frame.empty:
            return []

        rows: list[dict[str, Any]] = []
        columns = {str(column).strip().lower(): column for column in frame.columns}
        for _, row in frame.iterrows():
            rows.append(
                {
                    "ts_code": self.normalize_symbol(str(self._value(row, columns, "ts_code", "symbol") or normalized)),
                    "trade_date": str(self._value(row, columns, "trade_date", "date") or ""),
                    "open": self._float(row, columns, "open"),
                    "high": self._float(row, columns, "high"),
                    "low": self._float(row, columns, "low"),
                    "close": self._float(row, columns, "close"),
                    "pre_close": self._float(row, columns, "pre_close"),
                    "pct_chg": self._float(row, columns, "pct_chg", "pct_change"),
                    "amount": self._float(row, columns, "amount"),
                }
            )
        rows.sort(key=lambda item: item["trade_date"])
        return rows

    def normalize_symbol(self, symbol: str) -> str:
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

    def _fetch_from_tushare(self, symbols: list[str]) -> list[Quote]:
        import tushare as ts

        if settings.tushare_base_url:
            return self._fetch_from_tushare_proxy(ts, symbols)

        if settings.tushare_token:
            ts.set_token(settings.tushare_token)
        frame = ts.realtime_quote(ts_code=",".join(symbols))
        if frame is None or frame.empty:
            return []
        return self._frame_to_quotes(frame, source="tushare_realtime")

    def _fetch_from_tushare_proxy(self, ts: Any, symbols: list[str]) -> list[Quote]:
        pro = self._proxy_pro_api(ts)
        frame = pro.realtime_quote(ts_code=",".join(symbols))
        if frame is not None and not frame.empty:
            return self._frame_to_quotes(frame, source="tushare_proxy_realtime")
        logger.info("tushare proxy realtime_quote returned no rows; falling back to sina realtime source")
        return self._fetch_from_sina_realtime(symbols)

    def _proxy_pro_api(self, ts: Any) -> Any:
        if settings.tushare_token:
            ts.set_token(settings.tushare_token)
        pro = ts.pro_api()
        if settings.tushare_token:
            setattr(pro, "_DataApi__token", settings.tushare_token)
            setattr(pro, "_DataApi_token", settings.tushare_token)
        if settings.tushare_base_url:
            setattr(pro, "_DataApi__http_url", settings.tushare_base_url.rstrip("/"))
        return pro

    def _fetch_from_sina_realtime(self, symbols: list[str]) -> list[Quote]:
        from tushare.stock.rtq import get_realtime_quotes_sina

        frame = get_realtime_quotes_sina(",".join(symbols))
        if frame is None or frame.empty:
            return []
        return self._frame_to_quotes(frame, source="tushare_sina_realtime_fallback")

    def _fetch_from_akshare(self, symbols: list[str]) -> list[Quote]:
        errors: list[str] = []
        for name, fetcher in (
            ("akshare_eastmoney_spot", self._fetch_from_akshare_em_spot),
            ("akshare_daily_latest", self._fetch_from_akshare_daily_latest),
            ("akshare_sina_spot", self._fetch_from_akshare_sina_spot),
        ):
            try:
                quotes = fetcher(symbols)
                if quotes:
                    logger.info("market data loaded from %s rows=%s", name, len(quotes))
                    return quotes
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                logger.warning("%s failed: %s", name, exc)
        if errors:
            logger.warning("all akshare fallback sources failed: %s", " | ".join(errors))
        return []

    def _fetch_from_akshare_em_spot(self, symbols: list[str]) -> list[Quote]:
        import akshare as ak

        frame = self._quiet_call(ak.stock_zh_a_spot_em)
        if frame is None or frame.empty:
            return []
        codes = {self._code_only(symbol) for symbol in symbols}
        columns = {str(column).strip(): column for column in frame.columns}
        code_column = columns.get("代码") or columns.get("code") or columns.get("CODE")
        if code_column is not None:
            frame = frame[frame[code_column].astype(str).str.zfill(6).isin(codes)]
        if frame.empty:
            return []
        return self._frame_to_quotes(frame, source="akshare_eastmoney_spot")

    def _fetch_from_akshare_sina_spot(self, symbols: list[str]) -> list[Quote]:
        import akshare as ak

        frame = self._quiet_call(ak.stock_zh_a_spot)
        if frame is None or frame.empty:
            return []
        codes = {self._code_only(symbol) for symbol in symbols}
        columns = {str(column).strip().lower(): column for column in frame.columns}
        code_column = columns.get("code") or columns.get("代码")
        if code_column is not None:
            frame = frame[frame[code_column].astype(str).str.zfill(6).isin(codes)]
        if frame.empty:
            return []
        return self._frame_to_quotes(frame, source="akshare_sina_spot")

    def _fetch_from_akshare_daily_latest(self, symbols: list[str]) -> list[Quote]:
        import akshare as ak

        rows: list[pd.DataFrame] = []
        end_date = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
        start_date = (datetime.now(BEIJING_TZ) - timedelta(days=15)).strftime("%Y%m%d")
        for symbol in symbols[: settings.max_candidates]:
            code = self._code_only(symbol)
            frame = self._quiet_call(
                ak.stock_zh_a_hist,
                symbol=code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )
            if frame is None or frame.empty:
                continue
            latest = frame.tail(1).copy()
            latest["代码"] = code
            latest["名称"] = self._known_name(symbol)
            rows.append(latest)
        if not rows:
            return []
        return self._frame_to_quotes(pd.concat(rows, ignore_index=True), source="akshare_daily_latest")

    def _frame_to_quotes(self, frame: pd.DataFrame, source: str) -> list[Quote]:
        columns = {str(column).strip().lower(): column for column in frame.columns}
        quotes: list[Quote] = []
        for _, row in frame.iterrows():
            symbol = self._value(row, columns, "ts_code", "symbol", "code", "代码", "股票代码")
            if not symbol:
                continue
            price = self._float(row, columns, "price", "close", "trade", "最新价", "收盘")
            pct_change = self._float(row, columns, "pct_change", "pct_chg", "changepercent", "涨跌幅")
            amount = self._float(row, columns, "amount_yi", "成交额")
            if amount == 0:
                raw_amount = self._float(row, columns, "amount")
                amount = raw_amount / 100000000 if raw_amount > 1000000 else raw_amount
            elif amount > 1000000:
                amount = amount / 100000000
            open_price = self._float(row, columns, "open", "今开", "开盘")
            high = self._float(row, columns, "high", "最高")
            low = self._float(row, columns, "low", "最低")
            pre_close = self._float(row, columns, "pre_close", "preclose", "pre_close_price", "昨收", "settlement")
            if pre_close == 0 and price > 0 and pct_change != -100:
                pre_close = price / (1 + pct_change / 100) if pct_change else 0.0
            if pct_change == 0 and price > 0 and pre_close > 0 and price != pre_close:
                pct_change = (price - pre_close) / pre_close * 100
            bid_price = self._float(row, columns, "bid_price", "bid", "b1_p", "bid1", "buy1", "buy")
            ask_price = self._float(row, columns, "ask_price", "ask", "a1_p", "ask1", "sell1", "sell")
            quotes.append(
                Quote(
                    symbol=self.normalize_symbol(str(symbol)),
                    name=str(self._value(row, columns, "name", "stock_name", "名称") or symbol),
                    price=price,
                    pct_change=pct_change,
                    amount_yi=amount,
                    open=open_price,
                    high=high,
                    low=low,
                    pre_close=pre_close,
                    bid_price=bid_price,
                    ask_price=ask_price,
                    turnover_rate=self._float(row, columns, "turnover_rate", "turnover", "换手率"),
                    volume_ratio=self._float(row, columns, "volume_ratio", "vol_ratio", "量比") or 1.0,
                    source=source,
                    raw={str(key): self._json_safe(value) for key, value in row.to_dict().items()},
                )
            )
        return quotes

    def _dry_run_quotes(self, symbols: list[str]) -> list[Quote]:
        seed_symbols = symbols or self._default_symbols()
        names = {
            "000001.SZ": "Ping An Bank",
            "600519.SH": "Kweichow Moutai",
            "300750.SZ": "CATL",
            "002594.SZ": "BYD",
            "688981.SH": "SMIC",
        }
        quotes: list[Quote] = []
        for index, symbol in enumerate(seed_symbols[: settings.max_candidates]):
            base = 10 + index * 7.5
            pct = round(3.6 - index * 0.28, 2)
            price = round(base * (1 + pct / 100), 2)
            quotes.append(
                Quote(
                    symbol=symbol,
                    name=names.get(symbol, symbol),
                    price=price,
                    pct_change=pct,
                    amount_yi=round(8.5 - min(index, 8) * 0.55, 2),
                    open=round(base * 1.005, 2),
                    high=round(price * 1.018, 2),
                    low=round(price * 0.985, 2),
                    pre_close=round(base, 2),
                    bid_price=round(price - 0.01, 2),
                    ask_price=price,
                    turnover_rate=round(2.8 - min(index, 6) * 0.12, 2),
                    volume_ratio=round(1.8 - min(index, 6) * 0.08, 2),
                    source="dry_run",
                    raw={"rank": index + 1},
                )
            )
        return quotes

    def _default_symbols(self) -> list[str]:
        return ["000001.SZ", "600519.SH", "300750.SZ", "002594.SZ", "688981.SH"]

    def _code_only(self, symbol: str) -> str:
        return self.normalize_symbol(symbol).split(".", 1)[0]

    def _known_name(self, symbol: str) -> str:
        normalized = self.normalize_symbol(symbol)
        names = {
            "000001.SZ": "平安银行",
            "600519.SH": "贵州茅台",
            "300750.SZ": "宁德时代",
            "002594.SZ": "比亚迪",
            "688981.SH": "中芯国际",
        }
        return names.get(normalized, normalized)

    def _quiet_call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return func(*args, **kwargs)

    def _value(self, row: pd.Series, columns: dict[str, Any], *names: str) -> Any:
        for name in names:
            column = columns.get(name)
            if column is not None:
                return row.get(column)
        return None

    def _float(self, row: pd.Series, columns: dict[str, Any], *names: str) -> float:
        value = self._value(row, columns, *names)
        try:
            return float(value) if value not in {None, ""} else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _json_safe(self, value: Any) -> Any:
        if pd.isna(value):
            return None
        if hasattr(value, "item"):
            return value.item()
        return value
