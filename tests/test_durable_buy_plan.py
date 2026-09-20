import copy
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base
from app.models import PlatformSetting
from app.services.timezone import BEIJING_TZ
from app.trading.durable_plan import propose, gate, validate
from app.trading.buy_alerts import publish, read_ledger, deliver
from app.trading.types import Quote


class BuyPlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(Path('research/buy-plan-2026-09-20/plan.json').read_text())
        self.now = datetime(2026, 9, 21, 10, 0, tzinfo=BEIJING_TZ)
        self.quotes = [Quote(c['symbol'], c['name'], c['reference_close'], 0, 2,
            pe_ttm=c['reference_pe_ttm'], timestamp=self.now.isoformat(), raw={
                'trade_open': True, 'valuation_as_of': '20260918', 'pb': .89,
                'latest_report': c['review_period'], 'latest_ann': c['review_ann']}) for c in self.plan['candidates']]

    def buys(self, ledger=None):
        return [d for d in propose(self.plan, self.quotes, ledger or {}, self.now) if d.action in ('buy','add')]

    def test_shared_budget_and_lots(self):
        buys = self.buys()
        self.assertGreater(len(buys), 0)
        self.assertLessEqual(sum(d.raw['reserved_amount'] for d in buys), 40000)
        self.assertNotIn('300750.SZ', [d.symbol for d in buys])
        for d in buys:
            self.assertLessEqual(d.raw['reserved_amount'], 20000)
            self.assertEqual(d.raw['planned_shares'] % 100, 0)
            c = next(c for c in self.plan['candidates'] if c['symbol'] == d.symbol)
            q = next(q for q in self.quotes if q.symbol == d.symbol)
            self.assertLessEqual(q.pe_ttm * d.raw['max_limit_price'] / q.price, c['max_pe_ttm'])

    def test_stale_future_closed_no_data(self):
        for delta in (-181, 10):
            self.quotes = [replace(q, timestamp=(self.now + timedelta(seconds=delta)).isoformat()) for q in self.quotes]
            self.assertEqual(self.buys(), [])
        self.assertEqual([d for d in propose(self.plan, [], {}, self.now) if d.action=='buy'], [])

    def test_weekend_and_expired_account(self):
        self.now = self.now.replace(day=20)
        self.assertEqual(self.buys(), [])
        self.now = self.now.replace(day=21)
        self.plan['account_as_of']='2026-08-01'
        self.assertEqual(self.buys(), [])

    def test_new_report_blocks(self):
        self.quotes = [replace(q,raw={**q.raw,'latest_report':'20260930'}) for q in self.quotes]
        self.assertEqual(self.buys(), [])

    def test_corporate_action_and_halt(self):
        for key in ('corporate_action', 'st', 'suspended'):
            qs = [replace(q, raw={**q.raw,key:True}) for q in self.quotes]
            self.assertFalse([d for d in propose(self.plan,qs,{},self.now) if d.action=='buy'])

    def test_pending_reserves_budget_across_different_stocks(self):
        first = self.buys()
        ledger = {d.raw['signal_id']:{**d.raw,'symbol':d.symbol,'state':'sent'} for d in first}
        second = self.buys(ledger)
        self.assertFalse(set(d.symbol for d in first) & set(d.symbol for d in second))
        self.assertLessEqual(sum(d.raw['reserved_amount'] for d in first+second),40000)

    def test_missing_held_quote_blocks_entire_portfolio(self):
        self.plan['holdings']={'000333.SZ':{'shares':100,'last_buy_date':'2026-09-01'}}
        self.quotes=[q for q in self.quotes if q.symbol!='000333.SZ']
        self.assertEqual(self.buys(),[])

    def test_nan_invalid_budget(self):
        self.plan['single_name_cap']=float('nan')
        with self.assertRaises(ValueError): validate(self.plan)

    def test_star_minimum_and_increment(self):
        c = next(c for c in self.plan['candidates'] if c['symbol']=='688188.SH')
        c['review_status']='pass'
        self.plan['candidates']=[c]
        self.quotes=[replace(next(q for q in self.quotes if q.symbol==c['symbol']),price=40,pe_ttm=20)]
        d=self.buys()[0]
        self.assertGreaterEqual(d.raw['planned_shares'],200)
        self.assertNotEqual(d.raw['planned_shares'] % 100,0)

    def test_cash_reserve_and_drawdown(self):
        self.plan['available_cash']=81000
        self.assertEqual(self.buys(),[])

    @patch('app.trading.buy_alerts.settings')
    def test_buy_only_persistent_dedup_and_failed_delivery(self, settings):
        settings.trading_push_enabled=True; settings.trading_dry_run=False
        engine=create_engine('sqlite:///:memory:')
        PlatformSetting.__table__.create(engine)
        called=[]
        def sender(*args): called.append(args); return {'status':'sent'}
        ds=propose(self.plan,self.quotes,{},self.now)
        with Session(engine) as db:
            self.assertEqual(publish(db,self.plan,[replace(d,action='observe') for d in ds],sender)['status'],'no_buy_signal')
            self.assertEqual(publish(db,self.plan,[replace(d,action='reduce') for d in ds],sender)['status'],'no_buy_signal')
            self.assertEqual(publish(db,self.plan,ds,sender)['status'],'sent')
        with Session(engine) as db:
            self.assertEqual(publish(db,self.plan,ds,sender)['status'],'no_buy_signal')
            self.assertTrue(read_ledger(db))
        self.assertEqual(len(called),1)

    @patch('app.trading.buy_alerts.requests.post')
    @patch('app.trading.buy_alerts.settings')
    def test_http_200_business_error_is_failure(self, settings, post):
        settings.trading_feishu_webhook_url='https://example.invalid'
        post.return_value.ok=True; post.return_value.status_code=200
        post.return_value.json.return_value={'code':19001}
        self.assertEqual(deliver(self.plan,'example','abc')['status'],'failed')


if __name__=='__main__': unittest.main()
