from __future__ import annotations

from collections.abc import Iterable

from ..models import DailyBar, SentimentState, SentimentSnapshot, Signal


class NewHighPullbackStrategy:
    name = "new_high_pullback"

    def generate(self, histories: dict[str, list[DailyBar]], sentiment: SentimentSnapshot) -> list[Signal]:
        if sentiment.state in {SentimentState.ICE, SentimentState.DECLINE}:
            return []
        result: list[Signal] = []
        for symbol, bars in histories.items():
            if len(bars) < 25 or bars[-1].is_st:
                continue
            closes = [b.close for b in bars]
            current, prior = bars[-1], bars[-2]
            high_20 = max(closes[-21:-1])
            ma10 = sum(closes[-10:]) / 10
            prior_high = max(closes[-22:-2])
            had_new_high = prior.close >= prior_high * .995
            pullback = current.close < prior.close and current.close >= ma10 and current.close >= high_20 * .94
            volume_shrinks = current.volume < sum(b.volume for b in bars[-6:-1]) / 5
            if had_new_high and pullback and volume_shrinks:
                score = min(95, 65 + (current.close / ma10 - 1) * 500 + (1 - current.volume / max(prior.volume, 1)) * 10)
                result.append(Signal(self.name, symbol, current.date, round(score, 2), .06, "20日新高后缩量回踩，未破10日均线", stop_loss=round(current.close * .93, 3)))
        return sorted(result, key=lambda x: x.score, reverse=True)
