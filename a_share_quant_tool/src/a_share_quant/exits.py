from __future__ import annotations

from dataclasses import dataclass

from .models import DailyBar


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: str


class AggressiveExitRules:
    """Rules evaluated with available pre-open or intraday data, never hindsight."""

    def evaluate(self, *, bar: DailyBar, entry_price: float, high_watermark: float, holding_days: int, max_holding_days: int = 20) -> ExitDecision:
        if bar.open <= bar.pre_close * .97:
            return ExitDecision(True, "低开超过3%，集合竞价离场")
        if bar.high >= bar.pre_close * 1.05 and bar.close <= bar.high * .97:
            return ExitDecision(True, "高开冲高后回落超过3%")
        if bar.close <= entry_price * .92:
            return ExitDecision(True, "单票8%硬止损")
        if high_watermark > entry_price and bar.close <= high_watermark * .93:
            return ExitDecision(True, "高点回撤7%，移动止盈")
        if holding_days >= max_holding_days:
            return ExitDecision(True, "持有期上限")
        return ExitDecision(False, "继续持有")
