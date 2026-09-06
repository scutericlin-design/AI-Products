from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from stock_alpha.paper import Ledger, Quote, TZ, ACCOUNTS
from stock_alpha.live_data import parse_sina


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Ledger(Path(self.temp.name) / "paper.sqlite")
        self.ledger.initialize()
        self.now = datetime(2026, 9, 7, 10, 0, tzinfo=TZ)
        self.symbol = "600000.SH"
        self.quote = Quote(self.symbol, "Test", 10, self.now, "test", 9.99, 10.01, 10_000_000, 11, 9)
        self.plan = {"ready": True, "weights": {self.symbol: 0.08}, "plan_id": "one", "ai_mode": "ai_reviewed"}

    def run_cycle(self, account="C_ai", quote=None, now=None, plan=None, **kwargs):
        q = quote or self.quote
        return self.ledger.execute(account, plan or self.plan, {q.symbol: q}, now or self.now,
                                   factors={q.symbol: 1.0}, **kwargs)

    def test_isolated_initial_million_and_no_reset(self):
        self.run_cycle()
        first = self.ledger.summary()
        self.assertLess(first["accounts"]["C_ai"]["cash"], 1_000_000)
        for account in ("A_baseline", "B_enhanced"):
            self.assertEqual(first["accounts"][account]["cash"], 1_000_000)
            self.assertEqual(first["accounts"][account]["trades"], 0)
        self.ledger.initialize()
        self.assertEqual(first, self.ledger.summary())

    def test_missing_initialized_db_cannot_reseed(self):
        self.ledger.path.unlink()
        with self.assertRaises(RuntimeError):
            self.ledger.initialize()

    def test_corrupt_db_cannot_reset(self):
        self.ledger.path.write_bytes(b"broken")
        with self.assertRaises(Exception):
            self.ledger.initialize()
        self.assertEqual(self.ledger.path.read_bytes(), b"broken")

    def test_same_cycle_and_same_plan_are_idempotent(self):
        self.assertEqual(len(self.run_cycle()["orders"]), 1)
        self.assertTrue(self.run_cycle()["duplicate_cycle"])
        later = self.now + timedelta(minutes=2)
        self.assertEqual(self.run_cycle(now=later, quote=replace(self.quote, at=later))["orders"], [])
        self.assertEqual(self.ledger.summary()["accounts"]["C_ai"]["trades"], 1)

    def test_t1_then_next_day_sell_and_no_same_day_reentry(self):
        self.run_cycle()
        later = self.now + timedelta(minutes=2)
        sell = {"ready": True, "weights": {}, "plan_id": "two"}
        blocked = self.run_cycle(plan=sell, now=later, quote=replace(self.quote, at=later))
        self.assertFalse(blocked["orders"])
        self.assertTrue(any(x["reason"] == "t_plus_one_or_volume_limit" for x in blocked["blocked"]))
        tomorrow = self.now + timedelta(days=1)
        sold = self.run_cycle(plan=sell, now=tomorrow, quote=replace(self.quote, at=tomorrow))
        self.assertEqual(sold["orders"][0]["side"], "SELL")
        rebuy = self.run_cycle(plan={**self.plan, "plan_id": "three"}, now=tomorrow + timedelta(minutes=2),
                               quote=replace(self.quote, at=tomorrow + timedelta(minutes=2)))
        self.assertFalse(rebuy["orders"])
        self.assertEqual(self.ledger.summary()["accounts"]["C_ai"]["positions"], [])

    def test_stale_future_missing_offer_and_price_limit_quotes_block(self):
        for index, q in enumerate([
            replace(self.quote, at=self.now - timedelta(minutes=3)),
            replace(self.quote, at=self.now + timedelta(seconds=10)),
            replace(self.quote, ask=0), replace(self.quote, up_limit=0),
            replace(self.quote, price=10.98, ask=10.99), replace(self.quote, volume=0),
            replace(self.quote, name="*ST Test"),
        ]):
            when = self.now + timedelta(minutes=index * 2)
            if index == 1:
                q = replace(q, at=when + timedelta(seconds=10))
            if index >= 2:
                q = replace(q, at=when)
            self.assertFalse(self.run_cycle(now=when, quote=q)["orders"])

    def test_lunch_weekend_and_closing_auction_never_trade(self):
        for when in (self.now.replace(hour=12), self.now.replace(hour=14, minute=58), self.now - timedelta(days=1)):
            result = self.run_cycle(now=when, quote=replace(self.quote, at=when))
            self.assertTrue(result["no_orders"])

    def test_limits_reject_unsafe_plan_before_any_mutation(self):
        for weight in (0.5, float("nan"), -0.1):
            with self.assertRaises(ValueError):
                self.run_cycle(plan={**self.plan, "weights": {self.symbol: weight}})
        self.assertEqual(self.ledger.summary()["accounts"]["C_ai"]["cash"], 1_000_000)

    def test_fee_budget_and_round_lots(self):
        result = self.run_cycle()
        fill = result["orders"][0]
        self.assertEqual(fill["quantity"] % 100, 0)
        self.assertGreater(fill["fee"], 0)
        self.assertGreaterEqual(result["cash"], 0)
        self.assertLessEqual(fill["quantity"] * fill["price"] + fill["fee"], 80_000)
        self.assertAlmostEqual(result["cash"], 1_000_000 - fill["quantity"] * fill["price"] - fill["fee"])

    def test_star_minimum_lot(self):
        symbol = "688981.SH"
        q = replace(self.quote, symbol=symbol, price=500, ask=500, bid=500, up_limit=600, down_limit=400)
        result = self.run_cycle(quote=q, plan={**self.plan, "weights": {symbol: 0.08}})
        self.assertFalse(result["orders"])

    def test_corporate_action_freezes_mark_and_blocks_execution(self):
        self.run_cycle()
        old = self.ledger.holdings("C_ai")[self.symbol]["mark"]
        when = self.now + timedelta(days=1)
        result = self.run_cycle(now=when, quote=replace(self.quote, at=when, price=9, bid=9, ask=9),
                                corporate_actions={self.symbol})
        self.assertFalse(result["orders"])
        self.assertIn(self.symbol, result["corporate_action_pending"])
        self.assertEqual(self.ledger.holdings("C_ai")[self.symbol]["mark"], old)

    def test_only_official_account_enqueues_notifications(self):
        for account in ACCOUNTS:
            self.run_cycle(account=account)
        with self.ledger.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 1)

    def test_gap_stop_uses_available_price_not_stop_threshold(self):
        self.run_cycle()
        when = self.now + timedelta(days=1)
        q = replace(self.quote, at=when, price=9.1, bid=9.1, ask=9.11, down_limit=8, up_limit=12)
        result = self.run_cycle(now=when, quote=q)
        fill = result["orders"][0]
        self.assertEqual(fill["reason"], "hard_stop_8pct")
        self.assertLess(fill["price"], 9.1)

    def test_timestamped_sina_parser_maps_by_symbol(self):
        fields = ["Test", "10", "10", "10", "11", "9", "9.99", "10.01", "1000000", "10000000"] + ["0"] * 20 + ["2026-09-07", "10:00:00", "00"]
        text = 'var hq_str_sh600000="' + ",".join(fields) + '";\nvar hq_str_sz000001="";'
        result = parse_sina(text, {self.symbol}, {self.symbol: (11, 9)})
        self.assertEqual(set(result), {self.symbol})
        self.assertTrue(result[self.symbol].fresh(self.now))
        self.assertFalse(result[self.symbol].fresh(self.now + timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()
