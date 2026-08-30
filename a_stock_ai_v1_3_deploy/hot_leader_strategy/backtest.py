from __future__ import annotations

from math import sqrt
from statistics import mean
from typing import Any

from hot_leader_strategy.config import HotLeaderSettings
from hot_leader_strategy.storage import HotLeaderStore
from hot_leader_strategy.strategy import HotLeaderStrategy

def run_backtest(store:HotLeaderStore,settings:HotLeaderSettings,start:str,end:str)->dict[str,Any]:
    dates=store.trade_dates(start,end)
    if len(dates)<65:return _blocked(store,start,end,"历史日线不足")
    coverage=store.adjustment_coverage(start,end); ratio=coverage["adjusted"]/coverage["total"] if coverage["total"] else 0
    if ratio<.98:return _blocked(store,start,end,f"复权因子覆盖率 {ratio:.2%} 低于 98%，拒绝生成热点龙头伪回测")
    if not store.universe_as_of(dates[0]):return _blocked(store,start,end,"缺少点时点股票池，拒绝幸存者偏差回测")
    cash=1_000_000.; positions={}; curve=[]; trades=turnover=0.; engine=HotLeaderStrategy(settings); pending=[]
    for i,date in enumerate(dates[:-1]):
        # Execute plans one session later; a limit-up buy or limit-down sell remains unfilled.
        for order in pending:
            bar=store.bars(order["symbol"],date,1)
            if not bar or bar[-1].trade_date!=date or _blocked_price(order["symbol"],bar[-1],order["side"]):continue
            price=bar[-1].open*(1+(settings.paper_slippage_pct if order["side"]=="BUY" else -settings.paper_slippage_pct))
            if order["side"]=="SELL" and order["symbol"] in positions:
                p=positions.pop(order["symbol"]); amount=p["qty"]*price; cash+=amount-_fee(amount,settings)-amount*settings.paper_stamp_duty_rate; turnover+=amount; trades+=1
            elif order["side"]=="BUY" and order["symbol"] not in positions:
                equity=cash+sum(x["qty"]*x["last"] for x in positions.values()); budget=min(cash,equity*order["candidate"]["target_weight"]); qty=int(budget/price/settings.paper_lot_size)*settings.paper_lot_size
                amount=qty*price; fee=_fee(amount,settings)
                if qty and amount+fee<=cash: cash-=amount+fee; positions[order["symbol"]]={"qty":qty,"last":price,"entry":date,"age":0,"candidate":order["candidate"],"high":price}; turnover+=amount; trades+=1
        pending=[]
        for symbol,p in list(positions.items()):
            bar=store.bars(symbol,date,1)
            if bar: p["last"]=bar[-1].close; p["high"]=max(p["high"],bar[-1].high)
            p["age"]+=1; c=p["candidate"]; exit_now=p["last"]<=c["stop_loss"] or p["last"]>=c["take_profit"] or p["last"]<=p["high"]*(1-c["trailing_stop_pct"]) or p["age"]>=settings.max_holding_days
            if exit_now:pending.append({"side":"SELL","symbol":symbol})
        if date[:6]!=dates[i+1][:6]:
            symbols=store.universe_as_of(date)
            if not symbols:return _blocked(store,start,end,f"{date} 缺少点时点股票池")
            ins=[x for x in store.instruments(date) if x.symbol in symbols]; plan=engine.decide(as_of=date,instruments=ins,histories={x.symbol:store.bars(x.symbol,date) for x in ins},held_symbols=set(positions))
            target={x["symbol"]:x for x in plan["recommendations"]}
            pending.extend({"side":"SELL","symbol":s} for s in positions if s not in target)
            pending.extend({"side":"BUY","symbol":s,"candidate":c} for s,c in target.items() if s not in positions)
        equity=cash+sum(x["qty"]*x["last"] for x in positions.values()); curve.append(equity)
    returns=[curve[i]/curve[i-1]-1 for i in range(1,len(curve))]; peak=max_dd=0.; peak=curve[0]
    for value in curve:peak=max(peak,value);max_dd=min(max_dd,value/peak-1)
    vol=sqrt(mean(x*x for x in returns)-mean(returns)**2) if len(returns)>1 else 0.; total=curve[-1]/1_000_000-1
    metrics={"total_return_pct":round(total*100,3),"annual_return_pct":round(((1+total)**(252/max(len(curve),1))-1)*100,3),"max_drawdown_pct":round(max_dd*100,3),"sharpe":round(mean(returns)/vol*sqrt(252),3) if vol else None,"trade_count":int(trades),"turnover_multiple":round(turnover/1_000_000,3),"days":len(curve)}
    run=store.log_backtest("ok",start,end,metrics,{"parameters":settings.parameter_snapshot(),"data_integrity":{"adjustment_coverage":coverage,"adjustment_coverage_ratio":round(ratio,6)},"execution":"close_signal_next_open","survivorship":"hot_leader_universe point_in_time","costs":"slippage+commission+stamp_duty","unfilled":"limit-up buy/limit-down sell"},{"curve":curve}); return {"status":"ok","run_id":run,"metrics":metrics}
def _blocked(store:HotLeaderStore,start:str,end:str,reason:str)->dict[str,Any]:return {"status":"blocked","reason":reason,"run_id":store.log_backtest("blocked",start,end,{}, {"fail_closed":True},{"reason":reason})}
def _fee(amount:float,s:HotLeaderSettings)->float:return max(amount*s.paper_commission_rate,s.paper_min_commission)
def _blocked_price(symbol:str,bar:Any,side:str)->bool:
    limit=30 if symbol.endswith(".BJ") else 20 if symbol.startswith(("300","688")) else 10
    return bar.pct_chg>=limit-.1 if side=="BUY" else bar.pct_chg<=-limit+.1
