from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import re
from statistics import mean, median
from typing import Any
from zoneinfo import ZoneInfo

from hot_leader_strategy.config import HotLeaderSettings, load_settings
from hot_leader_strategy.data_client import HotLeaderDataClient
from hot_leader_strategy.notify import send_execution_report
from hot_leader_strategy.paper import ACCOUNT_ID, HotLeaderPaperRunner
from hot_leader_strategy.storage import HotLeaderStore
from hot_leader_strategy.strategy import HotLeaderStrategy
from scheduler.trading_calendar import current_trading_window

TZ=ZoneInfo("Asia/Shanghai")

class HotLeaderRunner:
    def __init__(self,settings:HotLeaderSettings|None=None,store:HotLeaderStore|None=None,client:HotLeaderDataClient|None=None):
        self.settings=settings or load_settings(); self.store=store or HotLeaderStore(self.settings.db_path); self.client=client or HotLeaderDataClient(self.settings); self.strategy=HotLeaderStrategy(self.settings)
    def refresh(self,start:str|None=None,end:str|None=None,limit:int|None=None)->dict[str,Any]:
        end=end or datetime.now(TZ).strftime("%Y%m%d"); start=start or end; limit=limit or self.settings.universe_limit
        if start:
            try:
                memberships=self.client.monthly_liquid_universe(start,end,limit)
            except Exception as exc:
                reason=f"无法建立点时点股票池，已拒绝写入不完整缓存：{exc}"
                self.store.log_quality("universe_preflight","blocked",reason,end)
                return {"status":"blocked","reason":reason,"storage":"hot_leader.sqlite"}
            if not memberships:
                reason="TuShare 未返回点时点股票池，已拒绝写入不完整缓存"
                self.store.log_quality("universe_preflight","blocked",reason,end)
                return {"status":"blocked","reason":reason,"storage":"hot_leader.sqlite"}
            try: instruments=self.client.instruments(include_inactive=True)
            except Exception as exc:
                reason=f"无法取得股票主数据：{exc}"
                self.store.log_quality("instrument_preflight","blocked",reason,end)
                return {"status":"blocked","reason":reason,"storage":"hot_leader.sqlite"}
            symbols={s for _,rows in memberships for s,_ in rows}; instruments=[x for x in instruments if x.symbol in symbols]
            for date,rows in memberships:self.store.upsert_universe(date,rows,"tushare:daily_basic_pit")
        else:
            try: instruments=self.client.instruments(include_inactive=False)[:limit]
            except Exception as exc:return {"status":"blocked","reason":f"无法取得股票主数据：{exc}","storage":"hot_leader.sqlite"}
        self.store.upsert_instruments(instruments); bars=failed=0
        for item in instruments:
            try: bars+=self.store.upsert_bars(self.client.daily_bars(item.symbol,start,end))
            except Exception as exc: failed+=1; self.store.log_quality("daily_bars","failed",f"{item.symbol}: {exc}")
        self.store.log_quality("refresh","ok" if not failed else "partial",f"instruments={len(instruments)} bars={bars} failures={failed}",end)
        return {"status":"ok" if not failed else "partial","instruments":len(instruments),"bars":bars,"failures":failed,"storage":"hot_leader.sqlite"}
    def signal_once(self,as_of:str|None=None)->dict[str,Any]:
        dates=self.store.trade_dates("20000101","20991231"); as_of=as_of or (dates[-1] if dates else "")
        if not as_of:return {"status":"blocked","reason":"热点龙头独立缓存为空；先执行 --refresh"}
        symbols=self.store.universe_as_of(as_of)
        if not symbols:return {"status":"blocked","reason":f"{as_of} 缺少点时点股票池，拒绝幸存者偏差信号"}
        instruments=[x for x in self.store.instruments(as_of) if x.symbol in symbols]; histories={x.symbol:self.store.bars(x.symbol,as_of) for x in instruments}
        held={x["symbol"] for x in self.store.paper_snapshot(ACCOUNT_ID,self.settings.paper_initial_cash)["positions"]}
        plan=self.strategy.decide(as_of=as_of,instruments=instruments,histories=histories,held_symbols=held); ident=self.store.save_signal(plan); self.store.save_theme_run(as_of,{"hot_themes":plan["hot_themes"],"market_state":plan["market_state"]})
        return {"status":"ok","signal_id":ident,"plan":plan}

    def refresh_live_cache(self, as_of: str | None = None) -> dict[str, Any]:
        """Refresh only enough whole-market history for next-session paper execution.

        This is intentionally cross-sectional and bounded; it never adopts the
        stock/ETF strategy cache or starts a multi-year bulk backfill.
        """
        end=as_of or datetime.now(TZ).strftime("%Y%m%d")
        start=(datetime.strptime(end,"%Y%m%d")-timedelta(days=self.settings.auto_refresh_calendar_days)).strftime("%Y%m%d")
        try:
            instruments=self.client.instruments(include_inactive=False); self.store.upsert_instruments(instruments)
            universe_date,members=self.client.liquid_universe_for_date(end,self.settings.universe_limit); self.store.upsert_universe(universe_date,members,"tushare:daily_basic_live_pit")
        except Exception as exc:
            reason=f"自动刷新前置数据失败：{exc}"; self.store.log_quality("live_preflight","blocked",reason,end); return {"status":"blocked","reason":reason}
        rows=adjusted=failures=0
        cursor=datetime.strptime(start,"%Y%m%d").date(); ceiling=datetime.strptime(end,"%Y%m%d").date()
        while cursor<=ceiling:
            date=cursor.strftime("%Y%m%d")
            try:
                bars=self.client.daily_bars_for_date(date)
                if bars:
                    factors=self.client.adjustment_factors_for_date(date)
                    rows+=self.store.upsert_bars([bar.__class__(**{**bar.__dict__,"adj_factor":factors.get(bar.symbol)}) for bar in bars]); adjusted+=sum(factors.get(bar.symbol) is not None for bar in bars)
            except Exception as exc:
                failures+=1; self.store.log_quality("live_cross_section", "failed", f"{date}: {exc}",date)
            cursor+=timedelta(days=1)
        status="ok" if rows and not failures else "partial" if rows else "blocked"; detail=f"universe={len(instruments)} members={len(members)} bars={rows} adjusted={adjusted} failures={failures}"; self.store.log_quality("live_refresh",status,detail,end)
        return {"status":status,"as_of":universe_date,"instruments":len(instruments),"universe_members":len(members),"bars":rows,"adjusted":adjusted,"failures":failures}

    def close_plan_once(self, as_of: str | None = None) -> dict[str, Any]:
        if not self.settings.auto_enabled:return {"status":"disabled","reason":"HOT_LEADER_AUTO_ENABLED=false"}
        refresh=self.refresh_live_cache(as_of)
        if refresh.get("status") not in {"ok","partial"}:return {"status":"blocked","refresh":refresh}
        result=self.signal_once(str(refresh.get("as_of") or "")); result["refresh"]=refresh; return result

    def execute_previous_plan_once(self, trade_date: str | None = None) -> dict[str, Any]:
        """Execute one previous-close plan at the next market open, at most once."""
        if not self.settings.auto_enabled:return {"status":"disabled","reason":"HOT_LEADER_AUTO_ENABLED=false","no_real_orders":True}
        if not self.settings.paper_enabled:return {"status":"disabled","reason":"HOT_LEADER_PAPER_ENABLED=false","no_real_orders":True}
        date=(trade_date or datetime.now(TZ).strftime("%Y-%m-%d")); compact=date.replace("-",""); latest=self.store.latest_signal(); plan=latest.get("payload") or {}; signal_id=str(latest.get("signal_id") or "")
        if not signal_id or not plan:return {"status":"no_pending_plan","no_real_orders":True}
        if str(plan.get("as_of") or "")>=compact:return {"status":"waiting_for_next_session","no_real_orders":True,"as_of":plan.get("as_of")}
        # When intraday monitoring is enabled, the opening execution becomes
        # the first price-confirmation pass. Later two-minute passes can still
        # enter a valid plan candidate if it is not chaseable at the open.
        if self.settings.intraday_enabled and compact == datetime.now(TZ).strftime("%Y%m%d"):
            result=self.intraday_once(); result["signal_id"]=signal_id
            if result.get("orders"):
                self.store.mark_signal_execution(signal_id,compact,str(result.get("status") or "ok"),f"intraday_quotes={result.get('quote_count',0)} orders={len(result.get('orders') or [])}")
            return result
        if self.store.signal_executed(signal_id):return {"status":"already_executed","no_real_orders":True,"signal_id":signal_id}
        symbols={str(x.get("symbol") or "") for x in plan.get("recommendations",[])}|{str(x.get("symbol") or "") for x in self.account().get("positions",[])}; symbols.discard("")
        try:
            quotes=self.client.realtime_prices(sorted(symbols))
        except Exception as exc:return {"status":"blocked","reason":f"开盘报价不可用，拒绝模拟成交：{exc}","no_real_orders":True}
        buys={str(x.get("symbol")) for x in plan.get("recommendations",[]) if str(x.get("action") or "").upper()=="BUY"}
        missing=buys-set(quotes)
        if missing:return {"status":"blocked","reason":f"开盘报价缺失 {','.join(sorted(missing)[:5])}，拒绝部分成交","no_real_orders":True}
        result=HotLeaderPaperRunner(self.settings,self.store).run(plan,date,quotes); self.store.mark_signal_execution(signal_id,compact,str(result.get("status") or "ok"),f"quotes={len(quotes)} orders={len(result.get('orders') or [])}"); result["signal_id"]=signal_id; result["push"]=send_execution_report(result,self.settings); return result

    def intraday_once(self, now: datetime | None = None) -> dict[str, Any]:
        """Run the isolated intraday confirmation and risk monitor once."""
        from hot_leader_strategy.intraday import HotLeaderIntradayMonitor

        return HotLeaderIntradayMonitor(settings=self.settings, store=self.store, client=self.client).run_once(now)

    def live_theme_once(self, now: datetime | None = None) -> dict[str, Any]:
        """Persist a current-session theme observation without changing paper orders."""
        checked_at = (now or datetime.now(TZ)).astimezone(TZ)
        window = current_trading_window(checked_at)
        if not self.settings.auto_enabled or not self.settings.live_theme_enabled:
            return {"status": "disabled", "no_real_orders": True, "checked_at": window.checked_at}
        if not window.is_open:
            return {"status": "skipped_outside_trading_window", "no_real_orders": True,
                    "checked_at": window.checked_at, "reason": window.reason}
        trade_date = checked_at.strftime("%Y%m%d")
        try:
            universe = self.store.universe_as_of(trade_date)
            if not universe:
                raise RuntimeError("缺少上一交易日流动性股票池")
            snapshot = self.client.realtime_market_quotes(sorted(universe))
            industries = {item.symbol: item.industry or "未分类" for item in self.store.instruments(trade_date)}
            quotes = [{**row, "industry": industries.get(row["symbol"], "未分类")} for row in snapshot["quotes"]
                      if row["symbol"] in universe and not _excluded_live_name(row["name"])]
            payload = _build_live_theme_payload(quotes, checked_at, self.settings)
            minimum_coverage = min(100, max(6, len(universe) // 10))
            if payload["quote_count"] < minimum_coverage:
                raise RuntimeError(f"流动性股票池实时覆盖不足：{payload['quote_count']}/{minimum_coverage}")
            run_id = self.store.save_live_theme_run(
                trade_date=trade_date, status="ok", source=str(snapshot["source"]), observed_at=str(snapshot["observed_at"]),
                provider_timestamp=snapshot.get("provider_timestamp"), universe_count=len(universe), quote_count=payload["quote_count"], payload=payload,
            )
            self.store.log_quality("live_theme", "ok", f"source={snapshot['source']} universe={len(universe)} quotes={payload['quote_count']} themes={len(payload['hot_themes'])}", trade_date)
            return {"status": "ok", "no_real_orders": True, "run_id": run_id, "checked_at": window.checked_at,
                    "observed_at": snapshot["observed_at"], "quote_count": payload["quote_count"], "hot_theme_count": len(payload["hot_themes"]),
                    "source": snapshot["source"]}
        except Exception as exc:
            detail = f"盘中热点扫描不可用：{type(exc).__name__}"
            self.store.log_quality("live_theme", "blocked", detail, trade_date)
            return {"status": "blocked", "no_real_orders": True, "checked_at": window.checked_at, "reason": detail}

    def paper_once(self,as_of:str|None=None)->dict[str,Any]:
        signal=self.signal_once(as_of); plan=signal.get("plan")
        if not plan:return {"status":"no_signal","orders":[],"no_real_orders":True,"reason":signal.get("reason")}
        if not self.settings.paper_enabled:return {"status":"disabled","orders":[],"no_real_orders":True,"plan":plan,"reason":"HOT_LEADER_PAPER_ENABLED=false"}
        result=HotLeaderPaperRunner(self.settings,self.store).run(plan); result["push"]=send_execution_report(result,self.settings); return result
    def account(self)->dict[str,Any]:return self.store.paper_snapshot(ACCOUNT_ID,self.settings.paper_initial_cash)


def _build_live_theme_payload(quotes: list[dict[str, Any]], checked_at: datetime, settings: HotLeaderSettings) -> dict[str, Any]:
    """Cross-sectional observation score, deliberately separate from daily alpha."""
    usable = [row for row in quotes if row["amount"] > 0 and row["industry"] not in {"", "未分类"}]
    market_change = mean(max(-10.0, min(10.0, float(row["pct_change"]))) for row in usable) if usable else 0.0
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in usable:
        grouped[str(row["industry"])].append(row)
    themes = []
    for theme, members in grouped.items():
        if len(members) < settings.min_theme_members:
            continue
        changes = [max(-10.0, min(10.0, float(row["pct_change"]))) for row in members]
        breadth = sum(change > 0 for change in changes) / len(changes)
        avg_change = mean(changes)
        turnover = median(float(row["turnover_rate"]) for row in members)
        amount_yi = sum(float(row["amount"]) for row in members) / 100_000_000
        relative = avg_change - market_change
        score = max(0, min(100, 50 + relative * 4 + (breadth - 0.5) * 32 + min(turnover, 10) * 1.2))
        hot = avg_change >= max(market_change + 0.6, 0.8) and breadth >= 0.60 and amount_yi >= 1
        leaders = sorted(members, key=lambda row: (float(row["pct_change"]), float(row["amount"]), float(row["turnover_rate"])), reverse=True)[:3]
        themes.append({"theme": theme, "score": round(score, 2), "hot": hot, "member_count": len(members),
                       "avg_change_pct": round(avg_change, 2), "relative_change_pct": round(relative, 2),
                       "breadth": round(breadth, 3), "turnover_rate": round(turnover, 2), "amount_yi": round(amount_yi, 2),
                       "leaders": [{key: leader[key] for key in ("symbol", "name", "price", "pct_change", "amount", "turnover_rate")} for leader in leaders]})
    themes.sort(key=lambda row: (not row["hot"], -row["score"], -row["amount_yi"], row["theme"]))
    return {"observation_only": True, "trade_effect": "none_next_session_research_context_only",
            "model": "intraday_theme_observation_v1", "market_avg_change_pct": round(market_change, 2),
            "quote_count": len(usable), "hot_themes": themes[:12], "generated_at": checked_at.isoformat(timespec="seconds"),
            "source_timestamp_available": False, "source_timestamp_note": "供应商未提供逐笔更新时间；仅展示本系统拉取时间。"}


def _excluded_live_name(name: str) -> bool:
    value = str(name).upper()
    return bool(re.match(r"^\*?ST", value)) or "退" in value
