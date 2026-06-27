from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="customer", index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    plan: Mapped[str] = mapped_column(String(32), default="free", index=True)
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    feature_flags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    sessions: Mapped[list["SessionToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    positions: Mapped[list["PortfolioPosition"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    watchlist: Mapped[list["WatchlistItem"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    advice_logs: Mapped[list["AdviceLog"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    trade_plan_reviews: Mapped[list["TradePlanReview"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    feedback_items: Mapped[list["FeedbackItem"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="FeedbackItem.user_id",
    )
    strategy_config: Mapped["StrategyConfig | None"] = relationship(back_populates="user", cascade="all, delete-orphan")
    strategy_versions: Mapped[list["StrategyVersion"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    export_requests: Mapped[list["DataExportRequest"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="DataExportRequest.user_id",
    )
    upgrade_requests: Mapped[list["PlanUpgradeRequest"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="PlanUpgradeRequest.user_id",
    )


class SessionToken(Base):
    __tablename__ = "session_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="sessions")


class PortfolioPosition(Base):
    __tablename__ = "portfolio_positions"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_user_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(128))
    weight: Mapped[float] = mapped_column(Float, default=0)
    cost_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    shares: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="positions")


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_user_watch_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="watchlist")


class AdviceLog(Base):
    __tablename__ = "advice_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    target_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="accepted")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    user: Mapped[User] = relationship(back_populates="advice_logs")


class TradePlanReview(Base):
    __tablename__ = "trade_plan_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(128))
    plan_action: Mapped[str] = mapped_column(String(64), index=True)
    planned_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    ideal_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_timing_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    odds_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision: Mapped[str] = mapped_column(String(32), default="watch", index=True)
    actual_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actual_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    plan_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="trade_plan_reviews")


class FeedbackItem(Base):
    __tablename__ = "feedback_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_code: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(32), default="suggestion", index=True)
    title: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text)
    contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_context: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    handled_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="feedback_items", foreign_keys=[user_id])


class StrategyConfig(Base):
    __tablename__ = "strategy_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    model_version: Mapped[str] = mapped_column(String(64), default="institutional_score_v7_short_profile_adaptive_tushare")
    strategy_type: Mapped[str] = mapped_column(String(64), default="short_elastic_2_8w")
    pool_limit: Mapped[int] = mapped_column(Integer, default=30)
    min_amount_yi: Mapped[float] = mapped_column(Float, default=3.0)
    buy_score_threshold: Mapped[float] = mapped_column(Float, default=78.0)
    min_pct_change: Mapped[float] = mapped_column(Float, default=1.0)
    max_pct_change: Mapped[float] = mapped_column(Float, default=9.7)
    min_close_position_pct: Mapped[float] = mapped_column(Float, default=55.0)
    max_amplitude_pct: Mapped[float] = mapped_column(Float, default=12.0)
    target_weight: Mapped[float] = mapped_column(Float, default=0.05)
    market_scope: Mapped[str] = mapped_column(String(128), default="main,chinext,star")
    include_beijing: Mapped[int] = mapped_column(Integer, default=0)
    strategy_profiles_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="strategy_config")


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    version_code: Mapped[str] = mapped_column(String(96), index=True)
    model_version: Mapped[str] = mapped_column(String(64))
    params_json: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    user: Mapped[User] = relationship(back_populates="strategy_versions")


class DataSourceConfig(Base):
    __tablename__ = "data_source_configs"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_user_data_provider"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="not_configured")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    api_token_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_mask: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class DataCacheEntry(Base):
    __tablename__ = "data_cache_entries"
    __table_args__ = (UniqueConstraint("provider", "dataset", "cache_key", name="uq_data_cache_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    dataset: Mapped[str] = mapped_column(String(64), index=True)
    cache_key: Mapped[str] = mapped_column(String(128), index=True)
    trade_date: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    payload_csv: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), index=True)


class DataExportRequest(Base):
    __tablename__ = "data_export_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    dataset: Mapped[str] = mapped_column(String(64), index=True)
    symbols_csv: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    end_date: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="export_requests", foreign_keys=[user_id])


class PlanUpgradeRequest(Base):
    __tablename__ = "plan_upgrade_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_plan: Mapped[str] = mapped_column(String(32), default="pro", index=True)
    billing_cycle: Mapped[str] = mapped_column(String(32), default="monthly")
    amount_cny: Mapped[float] = mapped_column(Float, default=99)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="upgrade_requests", foreign_keys=[user_id])


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(16))
    path: Mapped[str] = mapped_column(String(255), index=True)
    status_code: Mapped[int] = mapped_column(Integer, index=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0)
    response_bytes: Mapped[int] = mapped_column(Integer, default=0)
    client_host: Mapped[str | None] = mapped_column(String(96), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class TradingEngineRun(Base):
    __tablename__ = "trading_engine_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    trigger_source: Mapped[str] = mapped_column(String(32), default="scheduler", index=True)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    market_phase: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    quote_count: Mapped[int] = mapped_column(Integer, default=0)
    leader_count: Mapped[int] = mapped_column(Integer, default=0)
    decision_count: Mapped[int] = mapped_column(Integer, default=0)
    push_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TradingDecisionLog(Base):
    __tablename__ = "trading_decision_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    target_weight: Mapped[float] = mapped_column(Float, default=0)
    risk_level: Mapped[str] = mapped_column(String(32), default="normal", index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_flags: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class TradingPushLog(Base):
    __tablename__ = "trading_push_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(32), default="feishu", index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(96), index=True)
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
