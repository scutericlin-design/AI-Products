from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import Mock, patch

from hot_leader_strategy.config import load_settings
from hot_leader_strategy.intraday import HotLeaderIntradayMonitor
from hot_leader_strategy.models import DailyBar, Instrument
from hot_leader_strategy.paper import HotLeaderPaperRunner
from hot_leader_strategy.storage import HotLeaderStore
from hot_leader_strategy.strategy import HotLeaderStrategy
from hot_leader_strategy.runner import HotLeaderRunner
from hot_leader_strategy.data_client import HotLeaderDataClient
from hot_leader_strategy.dashboard import build_hot_leader_dashboard_payload


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

    def test_live_theme_observation_saves_current_session_without_orders(self)->None:
        settings=replace(self.settings,auto_enabled=True,live_theme_enabled=True,live_theme_interval_minutes=5)
        self.store.upsert_instruments(self.items)
        self.store.upsert_universe("20260818",[(item.symbol,100-i) for i,item in enumerate(self.items)],"test")
        quotes=[]
        for i,item in enumerate(self.items):
            quotes.append({"symbol":item.symbol,"name":item.name,"industry":item.industry,"price":10+i,
                           "pct_change":6.0 if i<3 else 0.5,"amount":100_000_000,"volume":1000000,
                           "amplitude_pct":4,"turnover_rate":5,"pre_close":9})
        client=_LiveThemeClient(quotes)
        result=HotLeaderRunner(settings=settings,store=self.store,client=client).live_theme_once(datetime(2026,8,19,10,0,tzinfo=ZoneInfo("Asia/Shanghai")))
        self.assertEqual(result["status"],"ok")
        self.assertTrue(result["no_real_orders"])
        latest=self.store.latest_live_theme_run()
        self.assertEqual(latest["quote_count"],6)
        self.assertTrue(latest["payload"]["observation_only"])
        self.assertEqual(latest["payload"]["trade_effect"],"none_next_session_research_context_only")
        self.assertTrue(any(theme["theme"]=="题材A" and theme["hot"] for theme in latest["payload"]["hot_themes"]))

    def test_live_theme_never_calls_source_when_closed(self)->None:
        settings=replace(self.settings,auto_enabled=True,live_theme_enabled=True)
        client=_LiveThemeClient([])
        result=HotLeaderRunner(settings=settings,store=self.store,client=client).live_theme_once(datetime(2026,8,19,12,0,tzinfo=ZoneInfo("Asia/Shanghai")))
        self.assertEqual(result["status"],"skipped_outside_trading_window")
        self.assertFalse(client.called)

    def test_public_live_theme_snapshot_is_normalized_and_bounded(self)->None:
        rows=[{"f12":f"{index:06d}","f14":f"测试{index}","f2":10,"f3":2,"f5":1000,"f6":10_000_000,"f7":3,"f8":2,"f18":9.8,"f100":"测试行业"} for index in range(1,101)]
        response=Mock(); response.raise_for_status.return_value=None; response.json.return_value={"data":{"diff":rows}}
        client=HotLeaderDataClient(self.settings)
        with patch("hot_leader_strategy.data_client._fetch_sina_batch",return_value=[{"symbol":"000001.SZ","provider_timestamp":"2026-08-19T10:00:00+08:00"} for _ in range(100)]) as fetch:
            result=client.realtime_market_quotes([f"{index:06d}.SZ" for index in range(1,101)])
        self.assertEqual(result["source"],"sina_timestamped_full_market")
        self.assertEqual(len(result["quotes"]),200)
        self.assertTrue(result["provider_timestamp_available"])
        self.assertEqual(fetch.call_count,2)

    def test_dashboard_exposes_live_theme_time_without_writing(self)->None:
        self.store.save_live_theme_run(trade_date="20260819",status="ok",source="test",observed_at="2026-08-19T10:00:00+08:00",provider_timestamp=None,universe_count=5000,quote_count=4000,payload={"hot_themes":[{"theme":"题材A","hot":True}],"market_avg_change_pct":1.2})
        with patch("hot_leader_strategy.dashboard.load_settings",return_value=replace(self.settings,live_theme_enabled=True,live_theme_interval_minutes=5)):
            payload=build_hot_leader_dashboard_payload()
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["live_theme"]["run"]["observed_at"],"2026-08-19T10:00:00+08:00")
        self.assertEqual(payload["live_theme"]["observation"]["hot_themes"][0]["theme"],"题材A")

    def test_old_ledger_without_live_theme_table_reads_as_no_snapshot(self)->None:
        old_path=Path(self.temp.name)/"old.sqlite"
        import sqlite3
        with sqlite3.connect(old_path) as db:
            db.execute("CREATE TABLE hot_leader_instruments (symbol TEXT)")
        self.assertEqual(HotLeaderStore(old_path,read_only=True).latest_live_theme_run(),{})

def _bars(symbol:str,start:float)->list[DailyBar]:
    result=[]
    for i in range(30):
        price=start*(1+i*.05); amount=250_000_000*(1+i/30); result.append(DailyBar(symbol,f"202607{(i+1):02d}",price*.995,price*1.02,price*.99,price,price*.975,2.5,amount,1_000_000,1,"test"))
    return result


class _QuoteClient:
    def __init__(self,prices:dict[str,float]):self.prices=prices
    def realtime_prices(self,symbols:list[str])->dict[str,float]:return {symbol:self.prices[symbol] for symbol in symbols if symbol in self.prices}

class _LiveThemeClient:
    def __init__(self,quotes):self.quotes=quotes;self.called=False
    def realtime_market_quotes(self,symbols):
        self.called=True
        return {"source":"test","observed_at":"2026-08-19T10:00:00+08:00","provider_timestamp":None,"quotes":self.quotes}

if __name__=="__main__":unittest.main()
