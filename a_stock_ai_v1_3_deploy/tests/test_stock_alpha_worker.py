from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from stock_alpha.live import Worker, process_lock
from stock_alpha.paper import TZ
from stock_alpha.live_data import LiveData


class WorkerTests(unittest.TestCase):
    def test_price_limits_are_symbol_scoped_and_date_checked(self):
        data = LiveData.__new__(LiveData)
        data.query = Mock(return_value=pd.DataFrame([
            ["600519.SH", "20260907", 110, 90],
            ["000001.SZ", "20260907", 22, 18],
            ["600519.SH", "20260904", 120, 80],
        ], columns=["ts_code", "trade_date", "up_limit", "down_limit"]))
        self.assertEqual(data.limits("20260907", ["600519.SH", "600519.SH"]), {"600519.SH": (110, 90)})
        self.assertEqual(data.query.call_count, 1)
        self.assertEqual(data.query.call_args.kwargs["ts_code"], "600519.SH")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.worker = Worker.__new__(Worker)
        self.worker.directory = Path(self.temp.name)
        self.worker.data = Mock()
        self.worker.ledger = Mock()
        self.worker.config = {}
        self.worker.health = Mock()
        self.worker.notify = Mock()

    def test_closed_session_never_calls_provider_or_ledger(self):
        for now in (datetime(2026, 9, 6, 10, tzinfo=TZ), datetime(2026, 9, 7, 12, tzinfo=TZ)):
            self.assertEqual(self.worker.cycle(now)["status"], "closed")
        self.worker.data.calendar.assert_not_called()
        self.worker.ledger.execute.assert_not_called()

    def test_exchange_holiday_never_executes(self):
        self.worker.data.calendar.return_value = ["20260904", "20260908"]
        self.assertEqual(self.worker.cycle(datetime(2026, 9, 7, 10, tzinfo=TZ))["status"], "exchange_holiday")
        self.worker.data.quotes.assert_not_called()
        self.worker.ledger.execute.assert_not_called()

    def test_network_failure_never_fabricates_fills(self):
        self.worker.data.calendar.side_effect = RuntimeError("calendar unavailable")
        with self.assertRaises(RuntimeError):
            self.worker.cycle(datetime(2026, 9, 7, 10, tzinfo=TZ))
        self.worker.ledger.execute.assert_not_called()

    def test_execution_clock_is_refreshed_after_network(self):
        initial = datetime(2026, 9, 7, 10, tzinfo=TZ)
        after_network = initial + timedelta(minutes=4)
        dates = iter([initial, after_network])

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return next(dates)

        self.worker.data.calendar.return_value = ["20260904", "20260907"]
        self.worker.ledger.state.side_effect = lambda key, default=None: "20260907" if key == "prepared_day" else {}
        self.worker.ledger.holdings.return_value = {}
        self.worker.ledger.execute.return_value = {"orders": []}
        self.worker.ledger.summary.return_value = {}
        self.worker.data.snapshot.return_value = {
            "as_of": "20260904",
            "bars": pd.DataFrame([{"ts_code": "600000.SH", "trade_date": "20260904", "adj_factor": 1.0}]),
            "metadata": pd.DataFrame([{"ts_code": "600000.SH", "name": "Test", "list_date": "20000101"}]),
        }
        self.worker.data.corporate_actions.return_value = set()
        self.worker.data.quotes.return_value = {}
        with patch("stock_alpha.live.datetime", Clock):
            self.worker.cycle()
        self.assertEqual(self.worker.ledger.execute.call_count, 3)
        for call in self.worker.ledger.execute.call_args_list:
            self.assertEqual(call.args[3], after_network)

    def test_worker_lock_prevents_second_writer(self):
        with process_lock(self.worker.directory):
            with self.assertRaises(BlockingIOError):
                with process_lock(self.worker.directory):
                    self.fail("Second writer acquired lock")


if __name__ == "__main__":
    unittest.main()
