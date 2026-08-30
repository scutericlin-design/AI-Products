from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ETFInstrument:
    symbol: str
    name: str
    index_code: str = ""
    index_name: str = ""
    exchange: str = ""
    etf_type: str = ""
    list_date: str = ""
    delist_date: str = ""
    list_status: str = ""


@dataclass(frozen=True)
class ETFBar:
    symbol: str
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    pre_close: float
    pct_chg: float
    volume: float
    amount: float
    source: str = "tushare"


@dataclass(frozen=True)
class ETFCandidate:
    symbol: str
    name: str
    bucket: str
    index_code: str
    index_name: str
    latest_price: float
    momentum_score: float
    annualized_trend: float
    r_squared: float
    ma10: float
    avg_turnover_yuan: float
    annualized_volatility: float
    three_day_min_return: float
    reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ETFDecisionPlan:
    strategy_id: str
    strategy_version: str
    as_of: str
    regime: str
    regime_detail: dict[str, Any]
    signal: str
    target_weight: float
    target: dict[str, Any] | None
    current_symbol: str | None
    trade_plan: list[dict[str, Any]]
    candidates: list[ETFCandidate]
    risk_flags: list[str] = field(default_factory=list)
    reasoning: str = ""
    intraday_confirmation: dict[str, Any] = field(default_factory=dict)
    nav_check: dict[str, Any] = field(default_factory=dict)
    ai_review: dict[str, Any] = field(default_factory=dict)
    execution_mode: str = "decision_support_only"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        return result
