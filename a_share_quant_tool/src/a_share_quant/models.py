from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class SentimentState(StrEnum):
    ICE = "ice"
    RECOVERY = "recovery"
    ADVANCE = "advance"
    EUPHORIA = "euphoria"
    DECLINE = "decline"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class DailyBar:
    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    pre_close: float
    volume: float = 0.0
    amount: float = 0.0
    suspended: bool = False
    is_st: bool = False
    name: str = ""

    @property
    def pct_change(self) -> float:
        return self.close / self.pre_close - 1 if self.pre_close > 0 else 0.0


@dataclass(frozen=True)
class SentimentMetrics:
    date: str
    limit_up_count: int
    limit_down_count: int
    max_board_height: int
    broken_board_rate: float
    yesterday_limit_up_return: float
    total_turnover: float
    turnover_ma5: float
    turnover_ma20: float
    margin_balance_momentum20: float | None = None
    high_board_breaks: int = 0
    theme_rotation_fast: bool = False


@dataclass(frozen=True)
class SentimentSnapshot:
    state: SentimentState
    target_exposure: float
    score: float
    reasons: tuple[str, ...]
    metrics: SentimentMetrics


@dataclass(frozen=True)
class Signal:
    strategy: str
    symbol: str
    date: str
    score: float
    target_weight: float
    reason: str
    stop_loss: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderIntent:
    client_order_id: str
    symbol: str
    side: Side
    quantity: int
    limit_price: float
    strategy: str
    signal_date: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
