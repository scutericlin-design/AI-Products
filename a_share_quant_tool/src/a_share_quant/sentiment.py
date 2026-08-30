from __future__ import annotations

from .models import SentimentMetrics, SentimentSnapshot, SentimentState


class SentimentEngine:
    """Close-of-day A-share emotion regime classifier.

    This is intentionally a rule engine instead of an optimized score.  Its
    output represents a *next-session* maximum exposure, never a same-day fill.
    """

    def evaluate(self, metrics: SentimentMetrics) -> SentimentSnapshot:
        reasons: list[str] = []
        turnover_ratio = metrics.total_turnover / metrics.turnover_ma20 if metrics.turnover_ma20 else 0.0
        growth = metrics.turnover_ma5 / metrics.turnover_ma20 - 1 if metrics.turnover_ma20 else 0.0
        negative_margin = metrics.margin_balance_momentum20 is not None and metrics.margin_balance_momentum20 < 0

        if ((metrics.limit_up_count < 25 and metrics.max_board_height <= 2 and metrics.broken_board_rate > .45)
                or metrics.limit_down_count >= metrics.limit_up_count):
            reasons.append("涨停广度低且炸板/跌停恶化")
            return self._snapshot(SentimentState.ICE, .20, 15, reasons, metrics)

        if (metrics.high_board_breaks >= 2 or metrics.theme_rotation_fast) and (
            metrics.limit_up_count < 50 or metrics.yesterday_limit_up_return < 0
        ):
            reasons.append("高位补跌或题材快速轮动")
            return self._snapshot(SentimentState.DECLINE, .20, 25, reasons, metrics)

        if metrics.limit_up_count >= 80 or metrics.max_board_height >= 7:
            reasons.append("涨停过热或高度过高，只减不加")
            return self._snapshot(SentimentState.EUPHORIA, .50, 80, reasons, metrics)

        if (metrics.limit_up_count > 50 and metrics.max_board_height >= 4
                and metrics.broken_board_rate < .40 and metrics.yesterday_limit_up_return > 0
                and turnover_ratio > 1.0):
            reasons.append("涨停广度、连板高度和昨日溢价共同确认")
            if turnover_ratio >= 1.3 or growth > .05:
                reasons.append("成交额增量确认")
            return self._snapshot(SentimentState.ADVANCE, 1.0, 70, reasons, metrics)

        if metrics.limit_up_count >= 40 and metrics.max_board_height >= 3 and metrics.broken_board_rate < .30:
            reasons.append("涨停回暖、新高度出现且炸板率回落")
            return self._snapshot(SentimentState.RECOVERY, .50, 50, reasons, metrics)

        reasons.append("信号不完整，按防御性修复处理")
        if negative_margin:
            reasons.append("两融余额20日动量转负")
        return self._snapshot(SentimentState.RECOVERY, .30, 35, reasons, metrics)

    @staticmethod
    def _snapshot(state: SentimentState, exposure: float, score: float, reasons: list[str], metrics: SentimentMetrics) -> SentimentSnapshot:
        return SentimentSnapshot(state=state, target_exposure=exposure, score=score, reasons=tuple(reasons), metrics=metrics)
