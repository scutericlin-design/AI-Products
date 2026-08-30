from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .models import SentimentSnapshot, SentimentState


@dataclass(frozen=True)
class RiskDecision:
    allowed_exposure: float
    allow_new_entries: bool
    force_liquidate: bool
    reasons: tuple[str, ...]


class PortfolioRiskEngine:
    def __init__(self, settings: Settings):
        self.settings = settings

    def evaluate(self, *, sentiment: SentimentSnapshot, portfolio_drawdown: float, consecutive_losses: int, data_fresh: bool) -> RiskDecision:
        reasons: list[str] = []
        if not data_fresh:
            return RiskDecision(0.0, False, False, ("数据不新鲜，禁止新建仓",))
        if portfolio_drawdown >= self.settings.max_drawdown_liquidate:
            return RiskDecision(0.0, False, True, ("组合回撤达到25%，清仓并进入冷静期",))
        exposure = sentiment.target_exposure
        allow = sentiment.state not in {SentimentState.ICE, SentimentState.DECLINE}
        if portfolio_drawdown >= self.settings.max_drawdown_stop:
            exposure = min(exposure, .20)
            allow = False
            reasons.append("组合回撤达到15%，停止加仓")
        elif portfolio_drawdown >= self.settings.max_drawdown_reduce:
            exposure = min(exposure, .50)
            reasons.append("组合回撤达到10%，目标仓位减半")
        if consecutive_losses >= 3:
            exposure *= .5
            reasons.append("连续3笔亏损，强制降半仓复盘")
        return RiskDecision(round(exposure, 4), allow, False, tuple(reasons or ["风险约束通过"]))
