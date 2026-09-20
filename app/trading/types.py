from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.timezone import now_beijing_iso


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    price: float
    pct_change: float
    amount_yi: float
    volume_ratio: float | None = None
    pe_ttm: float | None = None
    source: str = "unknown"
    timestamp: str = field(default_factory=now_beijing_iso)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketState:
    phase: str
    regime: str
    breadth: float
    avg_pct_change: float
    total_amount_yi: float
    quote_count: int
    note: str


@dataclass(frozen=True)
class LeaderCandidate:
    symbol: str
    name: str
    price: float
    pct_change: float
    amount_yi: float
    leader_score: float
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    symbol: str
    name: str
    action: str
    confidence: float
    target_weight: float
    reason: str
    risk_level: str = "normal"
    risk_flags: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)
