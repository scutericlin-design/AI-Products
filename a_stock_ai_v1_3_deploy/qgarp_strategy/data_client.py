from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from qgarp_strategy.config import QGARPSettings
from qgarp_strategy.models import DailyBar, FundamentalSnapshot, Instrument


logger = logging.getLogger(__name__)


class QGARPDataError(RuntimeError):
    pass


class QGARPDataClient:
    """Independent daily-data adapter: TuShare proxy first, AKShare price fallback second.

    AKShare is deliberately never used to fill missing financial or announcement data.
    """

    def __init__(self, settings: QGARPSettings):
        self.settings = settings
        self._pro_client: Any | None = None

    def fetch_universe(self, include_inactive: bool = False, limit: int | None = None) -> list[Instrument]:
        if self.settings.universe_symbols:
            return [Instrument(symbol=_symbol(value), name=value) for value in self.settings.universe_symbols]
        if self._tushare_available:
            try:
                pro = self._pro()
                frames = [pro.stock_basic(exchange="", list_status=status, fields="ts_code,name,industry,list_date,delist_date,list_status") for status in (["L", "D", "P"] if include_inactive else ["L"])]
                rows = []
                for frame in frames:
                    rows.extend(Instrument(symbol=_symbol(str(item.get("ts_code") or "")), name=str(item.get("name") or ""), industry=str(item.get("industry") or "未分类"), list_date=str(item.get("list_date") or ""), delist_date=str(item.get("delist_date") or ""), list_status=str(item.get("list_status") or "L")) for item in _records(frame) if item.get("ts_code"))
                effective_limit = None if include_inactive and limit is None else (self.settings.universe_limit if limit is None else limit)
                return rows if effective_limit is None else rows[:effective_limit]
            except Exception as exc:
                logger.warning("Q-GARP TuShare universe unavailable: %s", exc)
        if self.settings.akshare_enabled:
            try:
                import akshare as ak

                frame = ak.stock_info_a_code_name()
                rows = []
                for item in _records(frame):
                    code = str(item.get("code") or item.get("代码") or "")
                    if code:
                        rows.append(Instrument(symbol=_symbol(code), name=str(item.get("name") or item.get("名称") or code)))
                effective_limit = None if include_inactive and limit is None else (self.settings.universe_limit if limit is None else limit)
                return rows if effective_limit is None else rows[:effective_limit]
            except Exception as exc:
                logger.warning("Q-GARP AKShare universe unavailable: %s", exc)
        raise QGARPDataError("股票池不可用：请配置 QGARP_TUSHARE_TOKEN/TUSHARE_TOKEN，或启用 AKShare")

    def fetch_index_memberships(self, index_code: str, start_date: str, end_date: str) -> list[tuple[str, str, float, str]]:
        if not self._tushare_available:
            raise QGARPDataError("点时点指数成分股需要配置 TuShare Token 或 TuShare 中转地址")
        try:
            rows = []
            pro = self._pro()
            # The TuShare proxy caps index_weight responses at 10,000 rows. Use
            # month-sized point-in-time slices, matching the monthly rebalance and
            # preserving each eligible-universe snapshot without truncation.
            for chunk_start, chunk_end in _month_ranges(start_date, end_date):
                frame = pro.index_weight(index_code=index_code, start_date=chunk_start, end_date=chunk_end)
                for item in _records(frame):
                    raw_symbol = str(item.get("con_code") or "")
                    symbol = _symbol(raw_symbol) if raw_symbol else ""
                    date = str(item.get("trade_date") or "")
                    if symbol and date:
                        rows.append((date, symbol, _num(item.get("weight")), "tushare:index_weight"))
            if not rows:
                raise QGARPDataError(f"{index_code} 未返回指数成分历史")
            return list({(date, symbol): (date, symbol, weight, source) for date, symbol, weight, source in rows}.values())
        except Exception as exc:
            raise QGARPDataError(f"指数点时点成分不可用：{index_code}: {exc}") from exc

    def fetch_daily_basic_memberships(self, start_date: str, end_date: str, limit: int) -> list[tuple[str, str, float, str]]:
        """Build a point-in-time liquid A-share universe from the proxy's daily_basic snapshots.

        This mirrors the existing platform's historical-backtest universe construction
        and never substitutes a current constituent list for an old date.
        """
        if not self._tushare_available:
            raise QGARPDataError("点时点股票池需要配置 TuShare Token 或 TuShare 中转地址")
        rows: list[tuple[str, str, float, str]] = []
        fields = "ts_code,turnover_rate,volume_ratio,total_mv,circ_mv"
        for chunk_start, chunk_end in _month_ranges(start_date, end_date):
            frame, trade_date = self._daily_basic_snapshot(chunk_start, chunk_end, fields)
            if frame is None or not trade_date:
                continue
            records = _records(frame)
            selected = _select_blended_liquid_universe(records, limit)
            rows.extend((trade_date, symbol, score, "tushare:daily_basic_pit") for symbol, score in selected)
        if not rows:
            raise QGARPDataError("daily_basic 未返回任何点时点股票池")
        return list({(date, symbol): (date, symbol, score, source) for date, symbol, score, source in rows}.values())

    def _daily_basic_snapshot(self, start_date: str, end_date: str, fields: str) -> tuple[Any | None, str]:
        cursor = datetime.strptime(end_date, "%Y%m%d").date()
        floor = datetime.strptime(start_date, "%Y%m%d").date()
        for _ in range(15):
            if cursor < floor:
                break
            trade_date = cursor.strftime("%Y%m%d")
            frame = self._pro().daily_basic(trade_date=trade_date, fields=fields)
            if frame is not None and not frame.empty:
                return frame, trade_date
            cursor -= timedelta(days=1)
        return None, ""

    def fetch_daily_history(self, symbol: str, end_date: str | None = None, days: int = 150, start_date: str | None = None) -> list[DailyBar]:
        end = end_date or datetime.now().strftime("%Y%m%d")
        start = start_date or (datetime.strptime(end, "%Y%m%d") - timedelta(days=max(days * 2, 220))).strftime("%Y%m%d")
        if self._tushare_available:
            try:
                pro = self._pro()
                daily = pro.daily(ts_code=symbol, start_date=start, end_date=end)
                basic = pro.daily_basic(ts_code=symbol, start_date=start, end_date=end, fields="ts_code,trade_date,pe_ttm,pb")
                try:
                    factors = self.fetch_adjustment_factors(symbol, start, end)
                except Exception as exc:
                    # A missing adjustment endpoint must not silently become a data
                    # source switch.  The strategy records raw-price fallback instead.
                    logger.warning("Q-GARP adj_factor unavailable for %s: %s", symbol, exc)
                    factors = {}
                basics = {str(row.get("trade_date")): row for row in _records(basic)}
                rows = []
                for item in _records(daily):
                    date = str(item.get("trade_date") or "")
                    base = basics.get(date, {})
                    rows.append(_daily_bar(symbol, item, base, "tushare:daily_daily_basic", factors.get(date)))
                return sorted([row for row in rows if row.close > 0], key=lambda item: item.trade_date)[-days:]
            except Exception as exc:
                logger.warning("Q-GARP TuShare daily history unavailable for %s: %s", symbol, exc)
        if not self.settings.akshare_enabled:
            raise QGARPDataError(f"日线不可用：{symbol}")
        try:
            import akshare as ak

            frame = ak.stock_zh_a_hist(symbol=_code(symbol), period="daily", start_date=start, end_date=end, adjust="")
            rows = []
            for item in _records(frame):
                date = str(item.get("日期") or item.get("date") or "").replace("-", "")
                rows.append(DailyBar(symbol=symbol, trade_date=date, open=_num(item.get("开盘") or item.get("open")), high=_num(item.get("最高") or item.get("high")), low=_num(item.get("最低") or item.get("low")), close=_num(item.get("收盘") or item.get("close")), pre_close=_num(item.get("昨收") or item.get("pre_close")), pct_chg=_num(item.get("涨跌幅") or item.get("pct_chg")), volume=_num(item.get("成交量") or item.get("volume")), amount=_num(item.get("成交额") or item.get("amount")), source="akshare:stock_zh_a_hist"))
            return sorted([row for row in rows if row.close > 0], key=lambda item: item.trade_date)[-days:]
        except Exception as exc:
            raise QGARPDataError(f"AKShare 日线不可用：{symbol}: {exc}") from exc

    def fetch_adjustment_factors(self, symbol: str, start_date: str, end_date: str) -> dict[str, float | None]:
        """Read TuShare adjustment factors without changing the price fallback policy."""
        if not self._tushare_available:
            raise QGARPDataError("复权因子需要 TuShare Token 或中转地址")
        try:
            frame = self._pro().adj_factor(ts_code=symbol, start_date=start_date, end_date=end_date)
            return {str(row.get("trade_date")): _optional(row.get("adj_factor")) for row in _records(frame)}
        except Exception as exc:
            raise QGARPDataError(f"复权因子不可用：{symbol}: {exc}") from exc

    def fetch_adjustment_factors_for_date(self, trade_date: str) -> dict[str, float | None]:
        """Fetch one trading day's cross-section, avoiding one request per stock."""
        if not self._tushare_available:
            raise QGARPDataError("复权因子需要 TuShare Token 或中转地址")
        try:
            frame = self._pro().adj_factor(trade_date=trade_date)
            return {
                _symbol(str(row.get("ts_code") or "")): _optional(row.get("adj_factor"))
                for row in _records(frame)
                if row.get("ts_code")
            }
        except Exception as exc:
            raise QGARPDataError(f"全市场复权因子不可用：{trade_date}: {exc}") from exc

    def fetch_index_history(self, symbol: str, end_date: str | None = None, days: int = 150, start_date: str | None = None) -> list[DailyBar]:
        """Fetch benchmark index bars through the dedicated TuShare endpoint.

        ``daily`` serves A-share securities, while CSI indexes require
        ``index_daily``.  Treating an index as a stock silently yielded an empty
        benchmark and disabled the regime model in historical tests.
        """
        end = end_date or datetime.now().strftime("%Y%m%d")
        start = start_date or (datetime.strptime(end, "%Y%m%d") - timedelta(days=max(days * 2, 220))).strftime("%Y%m%d")
        if not self._tushare_available:
            raise QGARPDataError("指数日线需要 TuShare Token 或中转地址")
        try:
            frame = self._pro().index_daily(ts_code=symbol, start_date=start, end_date=end)
            rows = []
            for item in _records(frame):
                rows.append(
                    DailyBar(
                        symbol=symbol,
                        trade_date=str(item.get("trade_date") or ""),
                        open=_num(item.get("open")), high=_num(item.get("high")), low=_num(item.get("low")),
                        close=_num(item.get("close")), pre_close=_num(item.get("pre_close")),
                        pct_chg=_num(item.get("pct_chg")), volume=_num(item.get("vol")), amount=_num(item.get("amount")),
                        source="tushare:index_daily",
                    )
                )
            return sorted([row for row in rows if row.trade_date and row.close > 0], key=lambda item: item.trade_date)[-days:]
        except Exception as exc:
            raise QGARPDataError(f"指数日线不可用：{symbol}: {exc}") from exc

    def fetch_financial_history(self, symbol: str, start_date: str | None = None, end_date: str | None = None) -> list[FundamentalSnapshot]:
        if not self._tushare_available:
            raise QGARPDataError("财务因子没有 AKShare 回填路径；必须配置 TuShare Token 或 TuShare 中转地址")
        try:
            frame = self._pro().fina_indicator(
                ts_code=symbol,
                start_date=start_date or "20000101",
                end_date=end_date or datetime.now().strftime("%Y%m%d"),
                fields="ts_code,end_date,ann_date,roe,roe_waa,or_yoy,netprofit_yoy,ocfps,debt_to_assets",
            )
            rows = []
            for item in _records(frame):
                report_end = str(item.get("end_date") or "")
                available_at = str(item.get("ann_date") or "")
                if report_end and available_at:
                    rows.append(FundamentalSnapshot(symbol=symbol, report_end_date=report_end, available_at=available_at, roe=_optional(item.get("roe") or item.get("roe_waa")), revenue_yoy=_optional(item.get("or_yoy")), profit_yoy=_optional(item.get("netprofit_yoy")), operating_cashflow_per_share=_optional(item.get("ocfps")), debt_to_assets=_optional(item.get("debt_to_assets")), source="tushare:fina_indicator"))
            return sorted(rows, key=lambda item: (item.available_at, item.report_end_date))
        except Exception as exc:
            raise QGARPDataError(f"TuShare 财务数据不可用：{symbol}: {exc}") from exc

    def _pro(self) -> Any:
        if not self._tushare_available:
            raise QGARPDataError("缺少 TuShare Token 或中转地址")
        if self._pro_client is not None:
            return self._pro_client
        import tushare as ts

        if self.settings.tushare_token:
            ts.set_token(self.settings.tushare_token)
        # Keep the proxy invocation identical to the existing A-share and ETF
        # adapters. Some proxy gateways inspect these private client fields.
        pro = ts.pro_api()
        if self.settings.tushare_token:
            setattr(pro, "_DataApi__token", self.settings.tushare_token)
            setattr(pro, "_DataApi_token", self.settings.tushare_token)
        if self.settings.tushare_base_url:
            setattr(pro, "_DataApi__http_url", self.settings.tushare_base_url.rstrip("/"))
        self._pro_client = pro
        return self._pro_client

    @property
    def _tushare_available(self) -> bool:
        return bool(self.settings.tushare_token or self.settings.tushare_base_url)


def _daily_bar(symbol: str, row: dict[str, Any], basic: dict[str, Any], source: str, adj_factor: float | None = None) -> DailyBar:
    return DailyBar(symbol=symbol, trade_date=str(row.get("trade_date") or ""), open=_num(row.get("open")), high=_num(row.get("high")), low=_num(row.get("low")), close=_num(row.get("close")), pre_close=_num(row.get("pre_close")), pct_chg=_num(row.get("pct_chg")), volume=_num(row.get("vol")), amount=_num(row.get("amount")) * 1000, pe_ttm=_optional(basic.get("pe_ttm")), pb=_optional(basic.get("pb")), adj_factor=adj_factor, source=source)


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    return [{str(key): value for key, value in item.items()} for item in frame.to_dict("records")]


def _symbol(value: str) -> str:
    raw = value.strip().upper()
    if "." in raw:
        return raw
    return f"{raw.zfill(6)}.{ 'SH' if raw.startswith(('6', '68')) else 'SZ'}"


def _code(value: str) -> str:
    return value.split(".", 1)[0].zfill(6)


def _num(value: Any) -> float:
    try:
        parsed = float(value)
        return parsed if parsed == parsed else 0.0
    except (TypeError, ValueError):
        return 0.0


def _optional(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if parsed == parsed else None
    except (TypeError, ValueError):
        return None


def _select_blended_liquid_universe(records: list[dict[str, Any]], limit: int) -> list[tuple[str, float]]:
    rows = []
    for item in records:
        raw_symbol = str(item.get("ts_code") or "")
        symbol = _symbol(raw_symbol) if raw_symbol else ""
        turnover, circ_mv = _num(item.get("turnover_rate")), _num(item.get("circ_mv"))
        if not symbol or symbol.endswith(".BJ") or circ_mv <= 0:
            continue
        rows.append((symbol, turnover, circ_mv))
    cap = max(int(limit), 100)
    large = sorted((row for row in rows if row[1] >= 0.2), key=lambda row: (row[2], row[1]), reverse=True)[: cap // 2]
    active = sorted((row for row in rows if row[1] >= 1.0 and 300000 <= row[2] <= 12000000), key=lambda row: row[1] * row[2], reverse=True)[: cap - len(large)]
    selected: list[tuple[str, float]] = []
    seen: set[str] = set()
    for symbol, turnover, circ_mv in [*large, *active]:
        if symbol not in seen:
            seen.add(symbol)
            selected.append((symbol, turnover * circ_mv))
        if len(selected) >= cap:
            break
    return selected


def _month_ranges(start_date: str, end_date: str) -> list[tuple[str, str]]:
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    ranges: list[tuple[str, str]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        if month == 12:
            next_month = datetime(year + 1, 1, 1).date()
        else:
            next_month = datetime(year, month + 1, 1).date()
        chunk_start = max(start, datetime(year, month, 1).date())
        chunk_end = min(end, next_month - timedelta(days=1))
        ranges.append((chunk_start.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return ranges
