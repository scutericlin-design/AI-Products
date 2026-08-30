from __future__ import annotations

import pandas as pd

from ..models import SentimentState, SentimentSnapshot, Signal


class EarningsSurpriseStrategy:
    name = "earnings_surprise_low_base"

    def generate(self, forecasts: pd.DataFrame, sentiment: SentimentSnapshot) -> list[Signal]:
        if sentiment.state in {SentimentState.ICE, SentimentState.DECLINE}:
            return []
        signals: list[Signal] = []
        for row in forecasts.to_dict("records"):
            code = str(row.get("股票代码") or row.get("代码") or row.get("code") or "")
            name = str(row.get("股票简称") or row.get("名称") or row.get("name") or "")
            growth = _number(row, "预测净利润同比增长", "净利润变动幅度", "profit_growth_pct")
            profit = _number(row, "预测净利润", "净利润", "profit")
            nonrecurring_ratio = _number(row, "非经常性损益占比", "non_recurring_ratio")
            prior_30d = _number(row, "公告前30日涨幅", "prior_30d_return_pct")
            flags = " ".join(str(row.get(key) or "") for key in ("审计意见", "风险提示", "name", "名称"))
            if not code or "ST" in name.upper() or any(word in flags for word in ("非标", "立案", "退市")):
                continue
            if growth <= 100 or profit <= 50_000_000 or nonrecurring_ratio >= 50 or prior_30d >= 20:
                continue
            score = min(100, 50 + min(growth, 300) / 10 + min(profit / 100_000_000, 10) * 2 - nonrecurring_ratio * .15)
            signals.append(Signal(self.name, code, sentiment.metrics.date, round(score, 2), .08, "预告高增长、绝对利润达标、低位且风险过滤通过", stop_loss=.08, metadata={"name": name, "holding_days": "5-20"}))
        return sorted(signals, key=lambda x: x.score, reverse=True)


def _number(row: dict, *keys: str) -> float:
    for key in keys:
        raw = row.get(key)
        if raw is None:
            continue
        try:
            return float(str(raw).replace("%", "").replace(",", ""))
        except (TypeError, ValueError):
            continue
    return 0.0
