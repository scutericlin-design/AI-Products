from __future__ import annotations

import pandas as pd

from ..models import SentimentState, SentimentSnapshot, Signal


class LeaderboardStrategy:
    name = "leaderboard_delayed_follow"

    def __init__(self, seat_stats: pd.DataFrame):
        self.seat_stats = seat_stats

    def generate(self, details: pd.DataFrame, sentiment: SentimentSnapshot) -> list[Signal]:
        if sentiment.state in {SentimentState.ICE, SentimentState.DECLINE}:
            return []
        qualified = self.seat_stats[(self.seat_stats["win_rate_5d"] > .55) & (self.seat_stats["profit_loss_ratio"] > 1.2)]
        seats = set(qualified["seat"].astype(str))
        result: list[Signal] = []
        for row in details.to_dict("records"):
            seat = str(row.get("营业部名称") or row.get("seat") or "")
            net_buy = _num(row, "买入净额", "net_buy")
            code = str(row.get("代码") or row.get("股票代码") or row.get("code") or "")
            position = _num(row, "连板数", "board_height")
            if seat not in seats or not code or net_buy <= 0 or position >= 5:
                continue
            result.append(Signal(self.name, code, sentiment.metrics.date, 70.0, .05, "滚动两年胜率合格席位净买入，等待次日竞价承接确认", stop_loss=.05, metadata={"seat": seat, "exit_rule": "3日不涨即走"}))
        return result


def _num(row: dict, *names: str) -> float:
    for name in names:
        try:
            return float(str(row.get(name) or 0).replace(",", ""))
        except (TypeError, ValueError):
            pass
    return 0.0
