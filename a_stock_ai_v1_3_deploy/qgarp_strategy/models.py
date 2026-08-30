from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    industry: str = "未分类"
    list_date: str = ""
    delist_date: str = ""
    list_status: str = "L"


@dataclass(frozen=True)
class DailyBar:
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
    pe_ttm: float | None = None
    pb: float | None = None
    adj_factor: float | None = None
    source: str = ""


@dataclass(frozen=True)
class FundamentalSnapshot:
    symbol: str
    report_end_date: str
    available_at: str
    roe: float | None
    revenue_yoy: float | None
    profit_yoy: float | None
    operating_cashflow_per_share: float | None
    debt_to_assets: float | None
    source: str


@dataclass(frozen=True)
class FactorScore:
    symbol: str
    quality: float
    earnings: float
    valuation: float
    trend: float
    liquidity: float
    low_vol: float
    risk_penalty: float
    total_score: float
