from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from hot_leader_strategy.config import HotLeaderSettings
from hot_leader_strategy.models import DailyBar, Instrument


STRATEGY_ID = "hot_theme_leader"


class HotLeaderStrategy:
    """Two data-driven A-share leader patterns: continuation and pullback-reclaim.

    The close creates a plan; no strategy result is an executable same-close fill.
    """

    def __init__(self, settings: HotLeaderSettings): self.settings = settings

    def decide(self, *, as_of: str, instruments: list[Instrument], histories: dict[str,list[DailyBar]], held_symbols: set[str] | None=None) -> dict[str,Any]:
        held_symbols=held_symbols or set()
        rows=[]; rejected=[]
        for item in instruments:
            bars=histories.get(item.symbol) or []
            reason=self._reject(item,bars,as_of)
            if reason: rejected.append(f"{item.symbol}:{reason}"); continue
            rows.append({"instrument":item,"bars":bars,"raw":self._raw(bars)})
        themes=self._themes(rows)
        market=self._market_gate(rows,themes)
        hot={theme for theme,info in themes.items() if info["hot"]}
        candidates=[]
        for row in rows:
            theme=row["instrument"].industry or "未分类"
            if theme not in hot: continue
            candidate=self._candidate(row,themes[theme],theme in hot,held_symbols)
            if candidate: candidates.append(candidate)
        candidates.sort(key=lambda row: row["strategy_score"], reverse=True)
        selected=[]; theme_alloc=defaultdict(float)
        for candidate in candidates:
            if len(selected)>=self.settings.max_names: break
            theme=candidate["industry"]
            if theme_alloc[theme]+self.settings.max_single_weight > min(self.settings.max_total_exposure,self.settings.max_theme_exposure)+1e-9: continue
            selected.append(candidate); theme_alloc[theme]+=self.settings.max_single_weight
        if market["permission"] != "BUY_ALLOWED": selected=[]
        allocation=min(self.settings.max_total_exposure,self.settings.max_single_weight*len(selected))
        for candidate in selected: candidate["target_weight"]=round(allocation/len(selected),4)
        return {
            "strategy_id":STRATEGY_ID,"strategy_version":self.settings.strategy_version,"as_of":as_of,
            "signal":"BUY" if selected else "HOLD","execution":"next_session_open_only","target_exposure":round(sum(x["target_weight"] for x in selected),4),
            "market_state":market,"hot_themes":sorted((info for info in themes.values() if info["hot"]),key=lambda x:x["score"],reverse=True),
            "recommendations":selected,"candidate_count":len(rows),"rejected_count":len(rejected),"rejected_sample":rejected[:30],
            "risk_flags":["independent_paper_only","no_limit_up_entry","T_plus_1","no_averaging_down","hard_risk_rules_override_ai"],"parameters":self.settings.parameter_snapshot(),
        }

    def _reject(self,item:Instrument,bars:list[DailyBar],as_of:str)->str|None:
        if "ST" in item.name.upper() or "退" in item.name:return "st_or_delisting"
        if item.list_date and _days(item.list_date,as_of)<self.settings.min_listing_days:return "listing_age"
        if len(bars)<21:return "insufficient_history"
        if mean(max(b.amount,0) for b in bars[-20:])<self.settings.min_amount_yuan:return "illiquid"
        if bars[-1].close<=0 or bars[-1].pre_close<=0:return "invalid_price"
        if bars[-1].pct_chg>=self._limit_pct(item)-0.3:return "limit_up_or_near_limit"
        return None

    def _raw(self,bars:list[DailyBar])->dict[str,float]:
        closes=_adjusted_closes(bars)
        high10=max(closes[-10:]); current=closes[-1]
        return {"r5":current/closes[-6]-1,"r10":current/closes[-11]-1,"volume_ratio":bars[-1].amount/max(mean(b.amount for b in bars[-21:-1]),1),"drawdown10":1-current/high10,"above_ma5":float(current>=mean(closes[-5:])),"above_ma10":float(current>=mean(closes[-10:]))}

    def _themes(self,rows:list[dict[str,Any]])->dict[str,dict[str,Any]]:
        grouped=defaultdict(list)
        for row in rows: grouped[row["instrument"].industry or "未分类"].append(row)
        result={}
        all_returns=[row["raw"]["r5"] for row in rows]
        cross=mean(all_returns) if all_returns else 0
        for theme,members in grouped.items():
            r5=mean(x["raw"]["r5"] for x in members); breadth=mean(x["raw"]["r5"]>0 for x in members); turnover=mean(min(x["raw"]["volume_ratio"],4) for x in members)
            score=50+min(max((r5-cross)*100, -20),20)*1.4+(breadth-.5)*30+(turnover-1)*8
            result[theme]={"theme":theme,"member_count":len(members),"return_5d_pct":round(r5*100,2),"breadth":round(breadth,3),"volume_ratio":round(turnover,2),"score":round(max(0,min(100,score)),2),"hot":len(members)>=self.settings.min_theme_members and r5>=max(cross,0.015) and breadth>=.55 and turnover>=1.05}
        return result

    def _market_gate(self,rows:list[dict[str,Any]],themes:dict[str,dict[str,Any]])->dict[str,Any]:
        breadth=mean(row["raw"]["r5"]>0 for row in rows) if rows else 0
        hot=sum(x["hot"] for x in themes.values())
        permission="BUY_ALLOWED" if breadth>=.52 and hot>=self.settings.min_hot_themes else "NO_BUY"
        return {"permission":permission,"breadth":round(breadth,3),"hot_theme_count":hot,"reason":"市场广度与热点扩散同时确认" if permission=="BUY_ALLOWED" else "热点扩散或市场广度不足，禁止追逐龙头"}

    def _candidate(self,row:dict[str,Any],theme:dict[str,Any],is_hot:bool,held:set[str])->dict[str,Any]|None:
        raw,bars,item=row["raw"],row["bars"],row["instrument"]
        mode=""
        if raw["r5"]>=self.settings.trend_entry_min_return and raw["above_ma5"] and raw["volume_ratio"]>=1.15: mode="trend_continuation"
        elif raw["r10"]>=self.settings.pullback_min_return and .02<=raw["drawdown10"]<=self.settings.pullback_max_drawdown and raw["above_ma5"] and raw["volume_ratio"]>=1.10: mode="pullback_reclaim"
        elif item.symbol in held and raw["above_ma10"]: mode="hold_hysteresis"
        if not mode:return None
        bar=bars[-1]; leader_score=min(100,theme["score"]*.35+min(raw["r5"]/.25,1)*25+min(raw["r10"]/.4,1)*15+min(raw["volume_ratio"]/2,1)*15+(1-raw["drawdown10"]/max(self.settings.pullback_max_drawdown,.01))*10)
        if leader_score<60:return None
        return {"symbol":item.symbol,"name":item.name,"industry":item.industry,"action":"HOLD" if item.symbol in held else "BUY","pattern":mode,"trigger_price":round(bar.close,2),"current_price":round(bar.close,2),"target_weight":0.0,"stop_loss":round(bar.close*(1-self.settings.stop_loss_pct),2),"take_profit":round(bar.close*(1+self.settings.take_profit_pct),2),"trailing_stop_pct":self.settings.trailing_stop_pct,"strategy_score":round(leader_score,2),"theme":theme,"reasoning":f"{theme['theme']}热点强度{theme['score']:.0f}，5日板块收益{theme['return_5d_pct']:.1f}%，个股5日{raw['r5']:.1%}、量比{raw['volume_ratio']:.1f}；{mode}。","risk_flags":["热点龙头高波动","次日开盘才可成交","涨停/近涨停禁止追入"]}

    def _limit_pct(self,item:Instrument)->float:
        return 30. if item.symbol.endswith(".BJ") else 20. if item.symbol.startswith(("300","688")) else 10.


def _days(left:str,right:str)->int:
    try:
        from datetime import datetime
        return (datetime.strptime(right,"%Y%m%d")-datetime.strptime(left,"%Y%m%d")).days
    except ValueError:return 9999

def _adjusted_closes(bars:list[DailyBar])->list[float]:
    factors=[bar.adj_factor for bar in bars]
    if any(value is None or value<=0 for value in factors):return [bar.close for bar in bars]
    anchor=float(factors[-1] or 1.);return [bar.close*float(bar.adj_factor or anchor)/anchor for bar in bars]
