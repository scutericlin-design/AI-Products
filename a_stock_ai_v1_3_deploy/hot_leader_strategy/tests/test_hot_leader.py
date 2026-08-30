from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hot_leader_strategy.config import load_settings
from hot_leader_strategy.intraday import HotLeaderIntradayMonitor
from hot_leader_strategy.models import DailyBar, Instrument
from hot_leader_strategy.paper import HotLeaderPaperRunner
from hot_leader_strategy.storage import HotLeaderStore
from hot_leader_strategy.strategy import HotLeaderStrategy


class HotLeaderTests(unittest.TestCase):
    def setUp(self)->None:
        self.temp=tempfile.TemporaryDirectory(); root=Path(self.temp.name)
        self.settings=replace(load_settings(),storage_dir=root,db_path=root/"hot.sqlite",reports_dir=root/"reports",max_names=3,min_hot_themes=2,min_theme_members=3,trend_entry_min_return=.10,paper_enabled=True,push_enabled=False,dry_run=True)
        self.store=HotLeaderStore(self.settings.db_path)
        self.items=[Instrument(f"00000{i}.SZ",f"测试{i}","题材A" if i<=3 else "题材B","20100101") for i in range(1,7)]
        self.histories={x.symbol:_bars(x.symbol,10+i) for i,x in enumerate(self.items)}
    def tearDown(self)->None:self.temp.cleanup()
    def test_two_hot_themes_create_complete_tradable_plan(self)->None:
        plan=HotLeaderStrategy(self.settings).decide(as_of="20260801",instruments=self.items,histories=self.histories)
        self.assertEqual(plan["strategy_id"],"hot_theme_leader"); self.assertEqual(plan["market_state"]["permission"],"BUY_ALLOWED"); self.assertTrue(plan["recommendations"])
        candidate=plan["recommendations"][0]
        for key in ("symbol","name","trigger_price","target_weight","stop_loss","take_profit","reasoning","theme","pattern"):self.assertIn(key,candidate)
        self.assertLessEqual(sum(x["target_weight"] for x in plan["recommendations"]),self.settings.max_total_exposure+1e-9)
        by_theme={}
        for candidate in plan["recommendations"]: by_theme[candidate["industry"]]=by_theme.get(candidate["industry"],0)+candidate["target_weight"]
        self.assertTrue(all(weight<=self.settings.max_theme_exposure+1e-9 for weight in by_theme.values()))
    def test_near_limit_stock_is_hard_rejected(self)->None:
        histories=dict(self.histories); bars=list(histories[self.items[0].symbol]); histories[self.items[0].symbol]=bars[:-1]+[replace(bars[-1],pct_chg=9.9)]
        plan=HotLeaderStrategy(self.settings).decide(as_of="20260801",instruments=self.items,histories=histories)
        self.assertNotIn(self.items[0].symbol,{x["symbol"] for x in plan["recommendations"]})
    def test_paper_account_is_independent_and_has_no_live_orders(self)->None:
        plan=HotLeaderStrategy(self.settings).decide(as_of="20260801",instruments=self.items,histories=self.histories)
        result=HotLeaderPaperRunner(self.settings,self.store).run(plan,"2026-08-02")
        self.assertTrue(result["no_real_orders"]); self.assertTrue(result["orders"]); self.assertEqual(result["orders"][0]["strategy_id"],"hot_theme_leader")
    def test_paper_obeys_t_plus_one_before_a_stop_can_sell(self)->None:
        plan=HotLeaderStrategy(self.settings).decide(as_of="20260801",instruments=self.items,histories=self.histories)
        runner=HotLeaderPaperRunner(self.settings,self.store); runner.run(plan,"2026-08-02")
        stressed={**plan,"recommendations":[{**x,"action":"HOLD","current_price":x["stop_loss"]*.9} for x in plan["recommendations"]]}
        same_day=runner.run(stressed,"2026-08-02")
        self.assertFalse(any(x["side"]=="SELL" for x in same_day["orders"]))
        next_day=runner.run(stressed,"2026-08-03")
        self.assertTrue(any(x["side"]=="SELL" for x in next_day["orders"]))

    def test_intraday_monitor_confirms_once_and_updates_independent_paper_account(self)->None:
        settings=replace(
            self.settings,
            auto_enabled=True,
            intraday_enabled=True,
            intraday_entry_enabled=True,
            intraday_entry_min_change=-.02,
            intraday_entry_max_change=.045,
        )
        candidate={
            "symbol":self.items[0].symbol,"name":self.items[0].name,"industry":self.items[0].industry,"action":"BUY",
            "trigger_price":10.0,"current_price":10.0,"target_weight":.05,"stop_loss":9.2,"take_profit":11.8,
            "trailing_stop_pct":.07,"reasoning":"测试热点龙头计划",
        }
        self.store.save_signal({"as_of":"20260818","signal":"BUY","recommendations":[candidate]})
        monitor=HotLeaderIntradayMonitor(settings=settings,store=self.store,client=_QuoteClient({candidate["symbol"]:10.2}))
        now=datetime(2026,8,19,10,0,tzinfo=ZoneInfo("Asia/Shanghai"))
        first=monitor.run_once(now)
        self.assertTrue(first["no_real_orders"])
        self.assertTrue(any(order["side"]=="BUY" for order in first["orders"]))
        self.assertEqual(first["push"]["status"],"disabled")
        self.assertEqual(len(self.store.recent_intraday_events()),1)
        self.assertEqual(len(self.store.latest_intraday_snapshots()),1)

        repeated=monitor.run_once(now+timedelta(minutes=2))
        self.assertFalse(repeated["orders"])

    def test_intraday_monitor_honors_t_plus_one_and_sells_next_session(self)->None:
        settings=replace(self.settings,auto_enabled=True,intraday_enabled=True,intraday_entry_enabled=True)
        candidate={
            "symbol":self.items[0].symbol,"name":self.items[0].name,"industry":self.items[0].industry,"action":"BUY",
            "trigger_price":10.0,"current_price":10.0,"target_weight":.05,"stop_loss":9.2,"take_profit":11.8,
            "trailing_stop_pct":.07,"reasoning":"测试热点龙头计划",
        }
        self.store.save_signal({"as_of":"20260818","signal":"BUY","recommendations":[candidate]})
        monitor=HotLeaderIntradayMonitor(settings=settings,store=self.store,client=_QuoteClient({candidate["symbol"]:10.0}))
        opened=monitor.run_once(datetime(2026,8,19,10,0,tzinfo=ZoneInfo("Asia/Shanghai")))
        self.assertTrue(any(order["side"]=="BUY" for order in opened["orders"]))

        monitor.client=_QuoteClient({candidate["symbol"]:8.8})
        exited=monitor.run_once(datetime(2026,8,20,10,0,tzinfo=ZoneInfo("Asia/Shanghai")))
        self.assertTrue(any(order["side"]=="SELL" for order in exited["orders"]))
        self.assertEqual(exited["events"][0]["side"],"SELL")

def _bars(symbol:str,start:float)->list[DailyBar]:
    result=[]
    for i in range(30):
        price=start*(1+i*.05); amount=250_000_000*(1+i/30); result.append(DailyBar(symbol,f"202607{(i+1):02d}",price*.995,price*1.02,price*.99,price,price*.975,2.5,amount,1_000_000,1,"test"))
    return result


class _QuoteClient:
    def __init__(self,prices:dict[str,float]):self.prices=prices
    def realtime_prices(self,symbols:list[str])->dict[str,float]:return {symbol:self.prices[symbol] for symbol in symbols if symbol in self.prices}

if __name__=="__main__":unittest.main()
