from __future__ import annotations

import logging
import sys
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from hot_leader_strategy.config import HotLeaderSettings
from hot_leader_strategy.models import DailyBar, Instrument


logger=logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")


class HotLeaderDataError(RuntimeError): pass


class _PlatformProxyClient:
    """Small independent TuShare-proxy adapter; it does not share platform caches."""

    def __init__(self, base_url: str, token: str): self.base_url,self.token=base_url.rstrip("/"),token

    def __getattr__(self, api_name: str):
        def query(**params: Any) -> pd.DataFrame:
            fields=str(params.pop("fields", "") or "")
            last_error: Exception | None=None
            for attempt in range(3):
                try:
                    response=requests.post(self.base_url,json={"api_name":api_name,"token":self.token,"params":params,"fields":fields},timeout=30)
                    response.raise_for_status(); payload=response.json()
                    if payload.get("code") not in {0,"0"}:raise HotLeaderDataError(str(payload.get("msg") or f"TuShare proxy error {payload.get('code')}"))
                    data=payload.get("data") or {}; return pd.DataFrame(data.get("items") or [],columns=data.get("fields") or [])
                except Exception as exc:
                    last_error=exc
                    if attempt<2: continue
            raise HotLeaderDataError(f"{api_name} proxy request failed: {last_error}") from last_error
        return query


class HotLeaderDataClient:
    """Independent provider adapter: TuShare proxy first, AKShare price fallback."""
    def __init__(self,settings:HotLeaderSettings):
        self.settings=settings; self._client:Any|None=None; self._token=settings.tushare_token; self._base_url=settings.tushare_base_url
        if not self._base_url: self._load_platform_proxy_credentials()

    def _load_platform_proxy_credentials(self) -> None:
        """Read the already-configured local platform credential only when env lacks a proxy.

        The resolved secret remains in memory and is never written into this strategy's
        config, SQLite cache, reports, logs, or response payloads.
        """
        platform_root=Path(__file__).resolve().parents[2]
        if not (platform_root/"scripts"/"tushare_proxy_client.py").exists(): return
        try:
            root=str(platform_root)
            if root not in sys.path: sys.path.insert(0,root)
            from scripts.tushare_proxy_client import credentials_from_db
            credential=credentials_from_db()
            if credential.base_url and credential.token:
                self._base_url=credential.base_url; self._token=credential.token
                logger.info("hot-leader is using the existing platform TuShare proxy credential")
        except Exception as exc:
            logger.info("hot-leader platform proxy credential unavailable: %s",exc)
    def instruments(self,include_inactive:bool=False)->list[Instrument]:
        if self._available:
            try:
                statuses=["L","D","P"] if include_inactive else ["L"]
                result=[]
                for status in statuses:
                    for x in _records(self._pro().stock_basic(exchange="",list_status=status,fields="ts_code,name,industry,list_date,delist_date")):
                        if x.get("ts_code"):result.append(Instrument(_symbol(str(x["ts_code"])),str(x.get("name") or ""),str(x.get("industry") or "未分类"),str(x.get("list_date") or ""),str(x.get("delist_date") or "")))
                return result
            except Exception as exc: logger.warning("hot-leader universe unavailable: %s",exc)
        if self.settings.akshare_enabled:
            try:
                import akshare as ak
                return [Instrument(_symbol(str(x.get("code") or x.get("代码"))),str(x.get("name") or x.get("名称") or "")) for x in _records(ak.stock_info_a_code_name()) if x.get("code") or x.get("代码")]
            except Exception as exc: logger.warning("hot-leader AKShare universe unavailable: %s",exc)
        raise HotLeaderDataError("缺少可用 TuShare 中转/Token 与 AKShare 备用数据")
    def daily_bars(self,symbol:str,start:str,end:str)->list[DailyBar]:
        if self._available:
            try:
                daily=_records(self._pro().daily(ts_code=symbol,start_date=start,end_date=end)); factors={str(x.get("trade_date")): _optional(x.get("adj_factor")) for x in _records(self._pro().adj_factor(ts_code=symbol,start_date=start,end_date=end))}
                return sorted([DailyBar(symbol,str(x.get("trade_date") or ""),_num(x.get("open")),_num(x.get("high")),_num(x.get("low")),_num(x.get("close")),_num(x.get("pre_close")),_num(x.get("pct_chg")),_num(x.get("amount"))*1000,_num(x.get("vol")),factors.get(str(x.get("trade_date"))),"tushare:daily") for x in daily if _num(x.get("close"))>0],key=lambda x:x.trade_date)
            except Exception as exc: logger.warning("hot-leader Tushare bars unavailable %s: %s",symbol,exc)
        if self.settings.akshare_enabled:
            try:
                import akshare as ak
                rows=[]
                for x in _records(ak.stock_zh_a_hist(symbol=symbol.split(".")[0],period="daily",start_date=start,end_date=end,adjust="")):
                    date=str(x.get("日期") or x.get("date") or "").replace("-",""); rows.append(DailyBar(symbol,date,_num(x.get("开盘") or x.get("open")),_num(x.get("最高") or x.get("high")),_num(x.get("最低") or x.get("low")),_num(x.get("收盘") or x.get("close")),_num(x.get("昨收") or x.get("pre_close")),_num(x.get("涨跌幅") or x.get("pct_chg")),_num(x.get("成交额") or x.get("amount")),_num(x.get("成交量") or x.get("volume")),None,"akshare:daily"))
                return [x for x in rows if x.close>0]
            except Exception as exc: raise HotLeaderDataError(f"日线不可用 {symbol}: {exc}") from exc
        raise HotLeaderDataError(f"日线不可用 {symbol}")

    def daily_bars_for_date(self,trade_date:str)->list[DailyBar]:
        """Fetch one complete A-share cross-section through the proxy.

        A daily automatic refresh uses one request per date rather than thousands
        of per-symbol requests.  It is only valid with the TuShare proxy because
        AKShare cannot provide an equivalent auditable historical cross-section.
        """
        if not self._available: raise HotLeaderDataError("全市场日线需要 TuShare 中转或 Token")
        try:
            rows=[]
            for x in _records(self._pro().daily(trade_date=trade_date,fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount")):
                symbol=_symbol(str(x.get("ts_code") or ""))
                if symbol and _num(x.get("close"))>0:
                    rows.append(DailyBar(symbol,str(x.get("trade_date") or trade_date),_num(x.get("open")),_num(x.get("high")),_num(x.get("low")),_num(x.get("close")),_num(x.get("pre_close")),_num(x.get("pct_chg")),_num(x.get("amount"))*1000,_num(x.get("vol")),None,"tushare:daily_cross_section"))
            return rows
        except Exception as exc: raise HotLeaderDataError(f"全市场日线不可用 {trade_date}: {exc}") from exc

    def adjustment_factors_for_date(self,trade_date:str)->dict[str,float|None]:
        if not self._available: raise HotLeaderDataError("全市场复权因子需要 TuShare 中转或 Token")
        try:return {_symbol(str(x.get("ts_code") or "")):_optional(x.get("adj_factor")) for x in _records(self._pro().adj_factor(trade_date=trade_date,fields="ts_code,trade_date,adj_factor")) if x.get("ts_code")}
        except Exception as exc: raise HotLeaderDataError(f"全市场复权因子不可用 {trade_date}: {exc}") from exc

    def liquid_universe_for_date(self,trade_date:str,limit:int)->tuple[str,list[tuple[str,float]]]:
        floor=(datetime.strptime(trade_date,"%Y%m%d")-timedelta(days=14)).strftime("%Y%m%d"); frame,date=self._daily_basic(floor,trade_date); ranked=[]
        for x in _records(frame):
            symbol=_symbol(str(x.get("ts_code") or "")); turnover=_num(x.get("turnover_rate")); mv=_num(x.get("circ_mv"))
            if symbol and not symbol.endswith(".BJ") and turnover>=.3 and mv>0: ranked.append((symbol,turnover*mv))
        if not date or not ranked: raise HotLeaderDataError(f"daily_basic 未返回可用股票池 {trade_date}")
        return date,sorted(ranked,key=lambda x:x[1],reverse=True)[:limit]

    def realtime_prices(self,symbols:list[str])->dict[str,float]:
        if not symbols: return {}
        primary: Exception | None = None
        if self._available:
            try:
                frame=self._pro().realtime_quote(ts_code=",".join(symbols),fields="ts_code,price,last,close")
                prices={}
                for row in _records(frame):
                    symbol=_symbol(str(row.get("ts_code") or row.get("symbol") or "")); price=_num(row.get("price") or row.get("last") or row.get("close"))
                    if symbol and price>0:prices[symbol]=price
                if not prices: raise HotLeaderDataError("TuShare 中转实时行情返回为空")
                return prices
            except Exception as exc:
                primary = exc
        else:
            primary = HotLeaderDataError("实时成交价缺少 TuShare 中转或 Token")
        try:
            fallback = _fetch_sina_batch([_symbol(symbol) for symbol in symbols])
            prices = {str(row["symbol"]): float(row["price"]) for row in fallback if float(row.get("price") or 0) > 0}
            if prices:
                logger.info("hot-leader realtime quote fallback=sina_timestamped rows=%s", len(prices))
                return prices
        except HotLeaderDataError:
            pass
        raise HotLeaderDataError(f"实时成交价不可用：{type(primary).__name__}") from primary

    def realtime_market_quotes(self, symbols: list[str]) -> dict[str, Any]:
        """Fetch a timestamped full-universe observation in bounded Sina batches.

        The TuShare proxy currently returns empty real-time rows. This backup is
        an independent observation feed only: it cannot provide an execution
        price or create an order. Batches are parallel but bounded so an entire
        5k-name liquid universe does not monopolize the scheduler.
        """
        requested = sorted({_symbol(symbol) for symbol in symbols if _symbol(symbol) and not _symbol(symbol).endswith(".BJ")})
        if not requested:
            raise HotLeaderDataError("全市场热点扫描缺少股票池")
        batches = [requested[index:index + 50] for index in range(0, len(requested), 50)]
        quotes: list[dict[str, Any]] = []
        failures = 0
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="hot-leader-sina") as pool:
            futures = [pool.submit(_fetch_sina_batch, batch) for batch in batches]
            for future in as_completed(futures):
                try:
                    quotes.extend(future.result())
                except HotLeaderDataError:
                    failures += 1
        if len(quotes) < min(100, max(6, len(requested) // 10)):
            raise HotLeaderDataError(f"全市场热点快照覆盖不足：{len(quotes)}/{len(requested)}，失败批次={failures}")
        provider_times = [row["provider_timestamp"] for row in quotes if row.get("provider_timestamp")]
        return {"source": "sina_timestamped_full_market", "observed_at": datetime.now(TZ).isoformat(timespec="seconds"),
                "provider_timestamp": max(provider_times) if provider_times else None, "provider_timestamp_available": bool(provider_times),
                "quotes": quotes, "provider_row_count": len(quotes), "failed_batches": failures}
    def monthly_liquid_universe(self,start:str,end:str,limit:int)->list[tuple[str,list[tuple[str,float]]]]:
        if not self._available: raise HotLeaderDataError("历史点时点股票池需要 TuShare 中转或 Token")
        result=[]
        try:
            for month in _months(start,end):
                frame,date=self._daily_basic(month[0],month[1])
                records=_records(frame)
                ranked=[]
                for x in records:
                    symbol=_symbol(str(x.get("ts_code") or "")); turnover=_num(x.get("turnover_rate")); mv=_num(x.get("circ_mv"))
                    if symbol and not symbol.endswith(".BJ") and turnover>=.3 and mv>0: ranked.append((symbol,turnover*mv))
                result.append((date,sorted(ranked,key=lambda x:x[1],reverse=True)[:limit]))
        except Exception as exc:
            raise HotLeaderDataError(f"无法取得 TuShare daily_basic 点时点股票池：{exc}") from exc
        return [(d,x) for d,x in result if d and x]
    def _daily_basic(self,start:str,end:str)->tuple[Any,str]:
        cursor=datetime.strptime(end,"%Y%m%d").date(); floor=datetime.strptime(start,"%Y%m%d").date()
        for _ in range(12):
            if cursor<floor:break
            date=cursor.strftime("%Y%m%d"); frame=self._pro().daily_basic(trade_date=date,fields="ts_code,turnover_rate,circ_mv")
            if frame is not None and not frame.empty:return frame,date
            cursor-=timedelta(days=1)
        return None,""
    @property
    def _available(self)->bool:return bool(self._token or self._base_url)
    def _pro(self)->Any:
        if self._client is not None:return self._client
        if not self._available:raise HotLeaderDataError("TuShare 不可用")
        if self._base_url and self._token:
            self._client=_PlatformProxyClient(self._base_url,self._token); return self._client
        import tushare as ts
        if self._token:ts.set_token(self._token)
        client=ts.pro_api()
        if self._token:
            setattr(client,"_DataApi__token",self._token); setattr(client,"_DataApi_token",self._token)
        self._client=client; return client


def _records(frame:Any)->list[dict[str,Any]]: return [] if frame is None or getattr(frame,"empty",True) else [{str(k):v for k,v in x.items()} for x in frame.to_dict("records")]
def _num(value:Any)->float:
    try: result=float(value); return result if result==result else 0.
    except (ValueError,TypeError):return 0.
def _optional(value:Any)->float|None:
    try: result=float(value); return result if result==result else None
    except (ValueError,TypeError):return None
def _symbol(value:str)->str:
    raw=value.strip().upper()
    if not raw:return ""
    return raw if "." in raw else f"{raw.zfill(6)}.{'SH' if raw.startswith(('6','68')) else 'SZ'}"


def _fetch_sina_batch(symbols: list[str]) -> list[dict[str, Any]]:
    aliases = {f"{symbol.split('.')[1].lower()}{symbol[:6]}": symbol for symbol in symbols}
    try:
        response = requests.get("https://hq.sinajs.cn/list=" + ",".join(aliases), headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}, timeout=(4, 12), allow_redirects=False)
        response.raise_for_status()
        text = response.content.decode("gbk")
    except (requests.RequestException, UnicodeDecodeError) as exc:
        raise HotLeaderDataError(f"Sina 实时行情请求失败：{type(exc).__name__}") from exc
    results = []
    for match in re.finditer(r'var hq_str_(sh|sz)(\d{6})="(.*?)";', text):
        alias = match.group(1) + match.group(2)
        symbol = aliases.get(alias)
        if not symbol:
            continue
        try:
            row = next(csv.reader([match.group(3)]))
            if len(row) < 32:
                continue
            price, pre_close = _num(row[3]), _num(row[2])
            timestamp = datetime.fromisoformat(f"{row[30]}T{row[31]}").replace(tzinfo=TZ)
            if price <= 0 or pre_close <= 0:
                continue
            results.append({"symbol": symbol, "name": row[0], "price": price,
                            "pct_change": round((price / pre_close - 1) * 100, 4), "amount": _num(row[9]),
                            "volume": _num(row[8]), "amplitude_pct": round((_num(row[4]) - _num(row[5])) / pre_close * 100, 4),
                            "turnover_rate": 0.0, "pre_close": pre_close, "provider_timestamp": timestamp.isoformat(timespec="seconds")})
        except (ValueError, IndexError, TypeError):
            continue
    return results
def _months(start:str,end:str)->list[tuple[str,str]]:
    current=datetime.strptime(start,"%Y%m%d").replace(day=1); last=datetime.strptime(end,"%Y%m%d")
    result=[]
    while current<=last:
        nxt=(current.replace(day=28)+timedelta(days=4)).replace(day=1); result.append((max(current,datetime.strptime(start,"%Y%m%d")).strftime("%Y%m%d"),min(nxt-timedelta(days=1),last).strftime("%Y%m%d"))); current=nxt
    return result
