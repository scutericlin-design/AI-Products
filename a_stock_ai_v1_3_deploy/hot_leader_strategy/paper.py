from __future__ import annotations

from datetime import datetime
from math import floor
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from hot_leader_strategy.config import HotLeaderSettings
from hot_leader_strategy.storage import HotLeaderStore


TZ=ZoneInfo("Asia/Shanghai"); ACCOUNT_ID="hot_theme_leader_paper"

class HotLeaderPaperRunner:
    """Local-only paper account. No broker client or live-order code exists here."""
    def __init__(self,settings:HotLeaderSettings,store:HotLeaderStore): self.settings,self.store=settings,store
    def run(self,plan:dict[str,Any],trade_date:str|None=None,execution_prices:dict[str,float]|None=None)->dict[str,Any]:
        date=trade_date or datetime.now(TZ).strftime("%Y-%m-%d"); compact=date.replace("-","")
        account,positions=self.store.load_account(ACCOUNT_ID,self.settings.paper_initial_cash,date); orders=[]
        for p in positions.values():
            # A-share T+1: a local paper buy becomes sellable only on a later trading run.
            if _date_key(str(p.get("entry_date") or "")) < _date_key(date): p["available_quantity"]=int(p["quantity"])
        overrides=execution_prices or {}; plan_prices={str(symbol):float(price) for symbol,price in overrides.items() if float(price)>0}; plan_prices.update({str(x.get("symbol")):float(overrides.get(str(x.get("symbol"))) or x.get("current_price") or x.get("trigger_price") or 0) for x in plan.get("recommendations",[])})
        for p in positions.values():
            price=plan_prices.get(p["symbol"],0); bars=self.store.bars(p["symbol"],str(plan.get("as_of") or ""),1)
            p["last_price"]=price or (bars[-1].close if bars else p["last_price"]); p["high_watermark"]=max(float(p.get("high_watermark") or 0),p["last_price"])
        for symbol,p in list(positions.items()):
            price=float(p["last_price"]); trail=float(p.get("high_watermark") or price)*(1-float(p.get("trailing_stop_pct") or 0)); reason=""
            if price<=float(p.get("stop_loss") or 0):reason="硬止损"
            elif price>=float(p.get("take_profit") or 0):reason="止盈"
            elif price<=trail:reason="移动止盈"
            elif _holding_days(str(p.get("entry_date") or date),date)>=self.settings.max_holding_days:reason="最长持有期到期"
            if reason and p["available_quantity"]: orders.append(self._sell(account,positions,p,price,date,f"{compact}:SELL:{symbol}:{reason}",reason))
        for c in plan.get("recommendations",[]):
            symbol=str(c.get("symbol") or "")
            if str(c.get("action") or "").upper()!="BUY" or symbol in positions:continue
            candidate={**c,"execution_price":overrides.get(symbol)}
            order=self._buy(account,positions,candidate,date,f"{compact}:BUY:{symbol}")
            if order:orders.append(order)
        self.store.save_account(account,positions,orders,"filled" if orders else "no_fill")
        return {"status":"ok","mode":"hot_leader_local_paper_only","no_real_orders":True,"orders":orders,"account":self.store.paper_snapshot(ACCOUNT_ID,self.settings.paper_initial_cash),"plan":plan}
    def _buy(self,account:dict[str,Any],positions:dict[str,dict[str,Any]],c:dict[str,Any],date:str,key:str)->dict[str,Any]|None:
        if self.store.has_order(key):return None
        reference=float(c.get("execution_price") or c.get("trigger_price") or 0); price=reference*(1+self.settings.paper_slippage_pct); equity=float(account["cash"])+sum(float(x["quantity"])*float(x["last_price"]) for x in positions.values()); budget=min(float(account["cash"]),equity*min(float(c.get("target_weight") or 0),self.settings.max_single_weight)); qty=_quantity(budget,price,self.settings)
        if qty<=0:return None
        amount=qty*price; fee=_fee(amount,self.settings)
        if amount+fee>float(account["cash"]):return None
        account["cash"]=round(float(account["cash"])-amount-fee,4); symbol=str(c["symbol"])
        positions[symbol]={"symbol":symbol,"name":str(c.get("name") or symbol),"industry":str(c.get("industry") or "未分类"),"quantity":qty,"available_quantity":0,"avg_cost":round((amount+fee)/qty,6),"last_price":reference,"entry_date":date,"stop_loss":float(c.get("stop_loss") or 0),"take_profit":float(c.get("take_profit") or 0),"trailing_stop_pct":float(c.get("trailing_stop_pct") or 0),"high_watermark":reference,"strategy_reason":str(c.get("reasoning") or "")}
        return _order("BUY",c,qty,price,fee,0,key,"热点龙头信号触发")
    def _sell(self,account:dict[str,Any],positions:dict[str,dict[str,Any]],p:dict[str,Any],price:float,date:str,key:str,reason:str)->dict[str,Any]:
        qty=int(p["available_quantity"]); execution=price*(1-self.settings.paper_slippage_pct); amount=qty*execution; fee=_fee(amount,self.settings); tax=amount*self.settings.paper_stamp_duty_rate; account["cash"]=round(float(account["cash"])+amount-fee-tax,4); account["realized_pnl"]=round(float(account.get("realized_pnl") or 0)+amount-fee-tax-qty*float(p["avg_cost"]),4); del positions[p["symbol"]]
        return _order("SELL",p,qty,execution,fee,tax,key,reason)

def _quantity(budget:float,price:float,s:HotLeaderSettings)->int:return floor(budget/max(price,1e-9)/s.paper_lot_size)*s.paper_lot_size
def _fee(amount:float,s:HotLeaderSettings)->float:return round(max(amount*s.paper_commission_rate,s.paper_min_commission),4)
def _order(side:str,row:dict[str,Any],qty:int,price:float,fee:float,tax:float,key:str,reason:str)->dict[str,Any]: return {"order_id":uuid4().hex,"dedupe_key":key,"side":side,"symbol":str(row["symbol"]),"name":str(row.get("name") or row["symbol"]),"status":"filled","quantity":qty,"price":round(price,4),"commission":fee,"tax":round(tax,4),"trigger_price":row.get("trigger_price",price),"target_weight":row.get("target_weight",0),"stop_loss":row.get("stop_loss"),"take_profit":row.get("take_profit"),"reason":f"{reason}；{row.get('reasoning') or row.get('strategy_reason') or ''}","created_at":datetime.now(TZ).isoformat(timespec="seconds"),"strategy_id":"hot_theme_leader"}

def _date_key(value:str)->str:return "".join(ch for ch in value if ch.isdigit())[:8]
def _holding_days(entry_date:str,current_date:str)->int:
    try:return (datetime.strptime(_date_key(current_date),"%Y%m%d")-datetime.strptime(_date_key(entry_date),"%Y%m%d")).days
    except ValueError:return 0
