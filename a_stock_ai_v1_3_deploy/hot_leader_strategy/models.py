from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    industry: str = "未分类"
    list_date: str = ""
    delist_date: str = ""


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
    amount: float
    volume: float = 0.0
    adj_factor: float | None = None
    source: str = ""
