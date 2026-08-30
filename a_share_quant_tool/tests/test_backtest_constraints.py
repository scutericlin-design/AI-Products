import unittest

from a_share_quant.backtest import DailyBacktester
from a_share_quant.config import Settings
from a_share_quant.models import DailyBar, OrderIntent, Side


def order(day: str, side: Side, quantity: int = 100) -> OrderIntent:
    return OrderIntent("id-" + day + side, "000001.SZ", side, quantity, 10.0, "test", day, "test")


def bar(day: str, open_: float, close: float, pre_close: float = 10.0) -> DailyBar:
    return DailyBar("000001.SZ", day, open_, close, close, close, pre_close)


class BacktestConstraintTests(unittest.TestCase):
    def test_signal_is_executed_at_next_open_not_signal_close(self):
        result = DailyBacktester(Settings()).run(
            {"20260102": {"000001.SZ": bar("20260102", 10, 10)}, "20260103": {"000001.SZ": bar("20260103", 12, 12, 11.9)}},
            {"20260102": [order("20260102", Side.BUY)]},
        )
        self.assertEqual(result.trades[0]["date"], "20260103")
        self.assertEqual(result.trades[0]["price"], 12 * 1.001)


    def test_commission_has_minimum_and_sell_stamp_duty(self):
        engine = DailyBacktester(Settings())
        self.assertEqual(engine._fee(1000), 5)
        self.assertEqual(Settings().stamp_duty_rate, .0005)


    def test_buy_order_requires_full_lot(self):
        result = DailyBacktester(Settings()).run(
            {"20260102": {"000001.SZ": bar("20260102", 10, 10)}, "20260103": {"000001.SZ": bar("20260103", 10, 10)}},
            {"20260102": [order("20260102", Side.BUY, 150)]},
        )
        self.assertEqual(result.trades[0]["quantity"], 100)


    def test_limit_up_buy_remains_unfilled(self):
        result = DailyBacktester(Settings()).run(
            {"20260102": {"000001.SZ": bar("20260102", 10, 10)}, "20260103": {"000001.SZ": bar("20260103", 11, 11)}},
            {"20260102": [order("20260102", Side.BUY)]},
        )
        self.assertFalse(result.trades)
