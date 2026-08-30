from __future__ import annotations

import pandas as pd

from ..models import SentimentState, SentimentSnapshot, Signal


class FirstBoardStrategy:
    name = "sentiment_first_board"

    def generate(self, pool: pd.DataFrame, sentiment: SentimentSnapshot) -> list[Signal]:
        if sentiment.state is not SentimentState.ADVANCE or sentiment.metrics.broken_board_rate > .40:
            return []
        signals: list[Signal] = []
        for row in pool.to_dict("records"):
            code = str(row.get("代码") or row.get("code") or row.get("ts_code") or "")
            name = str(row.get("名称") or row.get("name") or "")
            seal_time = str(row.get("首次封板时间") or row.get("封板时间") or row.get("first_lu_time") or "")
            turnover = _num(row, "换手率", "turnover_rate")
            float_cap = _num(row, "流通市值", "float_market_cap", "free_float")
            seal_amount = _num(row, "封单资金", "seal_amount", "limit_amount")
            theme_count = _num(row, "板块涨停数", "theme_limit_up_count")
            one_word = bool(row.get("一字板") or row.get("is_one_word")) or "一字" in str(row.get("status") or "")
            if not code or one_word or not _before_1030(seal_time):
                continue
            if not (5 <= turnover <= 25 and 2_000_000_000 <= float_cap <= 10_000_000_000):
                continue
            if seal_amount / max(float_cap, 1) < .03 or theme_count < 3:
                continue
            score = min(100, 55 + (10.5 - _hour(seal_time)) * 7 + min(seal_amount / float_cap * 100, 10) * 2)
            signals.append(Signal(self.name, code, sentiment.metrics.date, round(score, 2), .10, "主升期首板：早封、封单强、换手与流通市值符合约束", metadata={"name": name, "seal_time": seal_time}))
        return sorted(signals, key=lambda x: x.score, reverse=True)


def _num(row: dict, *names: str) -> float:
    for name in names:
        try:
            value = row.get(name)
            if value is not None:
                return float(str(value).replace("%", ""))
        except (ValueError, TypeError):
            pass
    return 0.0


def _before_1030(value: str) -> bool:
    compact = _time(value)
    return compact[:4].isdigit() and int(compact[:4]) < 1030


def _hour(value: str) -> float:
    compact = _time(value)
    return int(compact[:2]) + int(compact[2:4]) / 60 if len(compact) >= 4 and compact[:4].isdigit() else 15.0


def _time(value: str) -> str:
    compact = str(value).replace(":", "").replace(".0", "")
    return compact.zfill(6) if compact.isdigit() and len(compact) <= 6 else compact
