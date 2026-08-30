import unittest

from a_share_quant.config import Settings
from a_share_quant.qmt import QmtBroker


class QmtGuardTests(unittest.TestCase):
    def test_live_is_blocked_by_default(self):
        check = QmtBroker(Settings()).preflight(
            data_fresh=True, reconciled=True, kill_switch=False, max_daily_loss=.03, max_orders=10,
        )
        self.assertFalse(check.ok)
        self.assertIn("ASQ_QMT_LIVE_ENABLED 未开启", check.reasons)
