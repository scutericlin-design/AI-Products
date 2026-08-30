from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .models import Signal
from .risk import RiskDecision


@dataclass(frozen=True)
class PortfolioPlan:
    signals: tuple[Signal, ...]
    target_new_exposure: float
    rejected: tuple[str, ...]


class PortfolioConstructor:
    """Turns ranked signals into bounded, retail-executable target weights."""

    def __init__(self, settings: Settings, max_names: int = 8, single_name_cap: float = .15):
        self.settings = settings
        self.max_names = max_names
        self.single_name_cap = single_name_cap

    def construct(self, signals: list[Signal], risk: RiskDecision, current_exposure: float) -> PortfolioPlan:
        if not risk.allow_new_entries:
            return PortfolioPlan((), 0.0, ("风险引擎禁止新开仓",))
        room = max(0.0, risk.allowed_exposure - current_exposure)
        cap = min(room, self.settings.max_daily_new_exposure)
        selected: list[Signal] = []
        rejected: list[str] = []
        used_symbols: set[str] = set()
        for signal in sorted(signals, key=lambda x: x.score, reverse=True):
            if len(selected) >= self.max_names:
                rejected.append(f"{signal.symbol}: 达到最大持仓数")
                continue
            if signal.symbol in used_symbols:
                rejected.append(f"{signal.symbol}: 多策略重复")
                continue
            target = min(signal.target_weight, self.single_name_cap, cap - sum(x.target_weight for x in selected))
            if target <= 0:
                rejected.append(f"{signal.symbol}: 无可用风险预算")
                continue
            selected.append(Signal(**{**signal.__dict__, "target_weight": round(target, 4)}))
            used_symbols.add(signal.symbol)
        return PortfolioPlan(tuple(selected), round(sum(x.target_weight for x in selected), 4), tuple(rejected))
