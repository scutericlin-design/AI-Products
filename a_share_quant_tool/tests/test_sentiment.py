import unittest

from a_share_quant.models import SentimentMetrics, SentimentState
from a_share_quant.sentiment import SentimentEngine


class SentimentTests(unittest.TestCase):
    def test_advance_regime_requires_breadth_height_and_positive_premium(self):
        snapshot = SentimentEngine().evaluate(SentimentMetrics(
            date="20260818", limit_up_count=62, limit_down_count=3, max_board_height=4,
            broken_board_rate=.22, yesterday_limit_up_return=.018, total_turnover=1.4,
            turnover_ma5=1.2, turnover_ma20=1.0,
        ))
        self.assertIs(snapshot.state, SentimentState.ADVANCE)
        self.assertEqual(snapshot.target_exposure, 1.0)


    def test_ice_regime_caps_exposure(self):
        snapshot = SentimentEngine().evaluate(SentimentMetrics(
            date="20260818", limit_up_count=20, limit_down_count=21, max_board_height=2,
            broken_board_rate=.50, yesterday_limit_up_return=-.02, total_turnover=1,
            turnover_ma5=1, turnover_ma20=1,
        ))
        self.assertIs(snapshot.state, SentimentState.ICE)
        self.assertLessEqual(snapshot.target_exposure, .2)
