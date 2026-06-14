from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class AuthRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class AuthResponse(BaseModel):
    token: str
    email: EmailStr


class PositionIn(BaseModel):
    symbol: str = Field(min_length=6, max_length=16)
    name: str | None = None
    weight: float = Field(ge=0, le=1)
    cost_price: float | None = Field(default=None, ge=0)
    shares: float | None = Field(default=None, ge=0)


class PositionOut(PositionIn):
    id: int


class WatchlistIn(BaseModel):
    symbol: str = Field(min_length=6, max_length=16)
    name: str
    note: str | None = None


class WatchlistOut(WatchlistIn):
    id: int


class StockLookupOut(BaseModel):
    symbol: str
    name: str
    source: str


class PortfolioAdviceOut(BaseModel):
    symbol: str
    name: str
    weight: float
    suggested_target_weight: float
    weight_delta: float
    portfolio_action: str
    signal_action: str
    price_factor_score: float | None = None
    latest_close: float | None = None
    cost_price: float | None = None
    pnl_pct: float | None = None
    shares: float | None = None
    trade_date: str | None = None
    risk_flags: str | None = None
    alpha_score: float | None = None
    liquidity_capacity_score: float | None = None
    risk_control_score: float | None = None
    crowding_penalty: float | None = None
    confidence: str | None = None
    model_version: str | None = None
    recommendation_tier: str | None = None
    signal_source: str | None = None
    advice_reason: str


class RiskSummaryOut(BaseModel):
    total_weight: float
    position_count: int
    max_single_weight: float
    reduce_count: int
    exit_count: int
    average_pnl_pct: float | None = None
    risk_level: str
    notes: list[str]


class AdviceLogIn(BaseModel):
    symbol: str = Field(min_length=6, max_length=16)
    name: str
    action: str
    target_weight: float | None = Field(default=None, ge=0, le=1)
    reason: str | None = None


class AdviceLogOut(AdviceLogIn):
    id: int
    status: str
    created_at: str


class StrategyConfigIn(BaseModel):
    pool_limit: int = Field(default=30, ge=5, le=30)
    min_amount_yi: float = Field(default=3.0, ge=0.5, le=100)
    buy_score_threshold: float = Field(default=78.0, ge=50, le=98)
    min_pct_change: float = Field(default=1.0, ge=-10, le=20)
    max_pct_change: float = Field(default=9.7, ge=1, le=30)
    min_close_position_pct: float = Field(default=55.0, ge=0, le=100)
    max_amplitude_pct: float = Field(default=12.0, ge=1, le=30)
    target_weight: float = Field(default=0.05, ge=0, le=0.2)
    markets: list[str] = Field(default_factory=lambda: ["main", "chinext", "star"])
    include_beijing: bool = False


class StrategyConfigOut(StrategyConfigIn):
    model_version: str


class StrategyVersionOut(BaseModel):
    id: int
    version_code: str
    model_version: str
    params: dict
    note: str | None = None
    created_at: str
