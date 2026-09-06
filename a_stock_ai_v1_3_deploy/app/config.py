from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


ENGINE_NAME = "A股市场实时感知引擎 v1.9"
ENGINE_POSITIONING = "market_realtime_perception_engine"
VALUE_STATEMENT = "v1.9用多策略组合适应市场风格，而不是让单一策略承担所有行情"

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")

MINIMAX_BASE_URL = "https://api.cg-czk.top/v1"
MINIMAX_ENDPOINT = f"{MINIMAX_BASE_URL.rstrip('/')}/chat/completions"
# Keep the relay model configurable for the current custom provider.
MINIMAX_MODEL = "stepfun-ai/step-3.7-flash"
MINIMAX_FALLBACK_MODEL = "minimaxai/minimax-m3"
MINIMAX_PROVIDER = "custom"
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")

STOCK_AI_PRIMARY_ENDPOINT = "https://tbtk.asia/v1/chat/completions"
STOCK_AI_PRIMARY_MODEL = "deepseek-v4-flash-0731"
STOCK_AI_SECONDARY_MODEL = "minimaxai/minimax-m3"
STOCK_AI_TERTIARY_MODEL = "stepfun-ai/step-3.7-flash"

FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK") or os.getenv("FEISHU_WEBHOOK_URL", "")

RUN_INTERVAL = 120

# Cross-sector liquid A-share sample used only for market breadth and sentiment.
# Stock selection remains controlled by WATCH_SYMBOLS and its own strategy filters.
DEFAULT_MARKET_SENTIMENT_SYMBOLS = [
    "000001.SZ", "000063.SZ", "000166.SZ", "000333.SZ", "000538.SZ", "000568.SZ",
    "000625.SZ", "000651.SZ", "000725.SZ", "000776.SZ", "000858.SZ", "000938.SZ",
    "001979.SZ", "002027.SZ", "002032.SZ", "002050.SZ", "002129.SZ", "002142.SZ",
    "002230.SZ", "002241.SZ", "002304.SZ", "002371.SZ", "002415.SZ", "002459.SZ",
    "002466.SZ", "002475.SZ", "002594.SZ", "002736.SZ", "002938.SZ", "300015.SZ",
    "300059.SZ", "300122.SZ", "300274.SZ", "300308.SZ", "300347.SZ", "300413.SZ",
    "300498.SZ", "300750.SZ", "300760.SZ", "600000.SH", "600015.SH", "600023.SH",
    "600028.SH", "600030.SH", "600036.SH", "600048.SH", "600050.SH", "600089.SH",
    "600111.SH", "600161.SH", "600196.SH", "600276.SH", "600309.SH", "600346.SH",
    "600406.SH", "600436.SH", "600487.SH", "600519.SH", "600547.SH", "600585.SH",
    "600660.SH", "600703.SH", "600809.SH", "600837.SH", "600900.SH", "600919.SH",
    "601012.SH", "601088.SH", "601127.SH", "601166.SH", "601211.SH", "601225.SH",
    "601238.SH", "601288.SH", "601318.SH", "601328.SH", "601336.SH", "601390.SH",
    "601398.SH", "601601.SH", "601628.SH", "601658.SH", "601668.SH", "601688.SH",
    "601728.SH", "601818.SH", "601857.SH", "601868.SH", "601899.SH", "601919.SH",
    "601939.SH", "601988.SH", "603259.SH", "603288.SH", "603369.SH", "603501.SH",
    "603986.SH", "688008.SH", "688012.SH", "688041.SH", "688396.SH", "688981.SH",
]


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    try:
        return int(value) if value not in {None, ""} else default
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    try:
        return float(value) if value not in {None, ""} else default
    except ValueError:
        return default


def _csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _choice_env(name: str, default: str, allowed: set[str]) -> str:
    value = os.getenv(name, default).strip().lower()
    return value if value in allowed else default


def _minimax_model(value: str | None) -> str:
    """Keep StepFun as the primary model even if a legacy value remains."""
    model = (value or MINIMAX_MODEL).strip()
    return MINIMAX_MODEL if model.startswith("minimaxai/") else model


def _fallback_model(value: str | None) -> str | None:
    model = (value or MINIMAX_FALLBACK_MODEL).strip()
    return model or None


@dataclass(frozen=True)
class Settings:
    app_name: str
    engine_name: str
    engine_positioning: str
    value_statement: str
    project_root: Path
    storage_dir: Path
    db_path: Path
    loop_seconds: int
    run_on_start: bool
    dry_run: bool
    akshare_enabled: bool
    watch_symbols: list[str]
    market_sentiment_symbols: list[str]
    stock_selection_universe_profile: str
    stock_selection_universe_limit: int
    stock_selection_universe_cache_path: Path
    stock_selection_universe_cache_hours: int
    max_candidates: int
    max_push_stocks: int
    min_turnover_yi: float
    confidence_threshold: float
    max_position_weight: float
    max_recommend_pct_change: float
    near_limit_up_buffer: float
    buy_range_pullback_pct: float
    max_chase_pct: float
    stop_loss_pct: float
    min_quality_score: float
    sentiment_enabled: bool
    sentiment_min_buy_score: float
    sentiment_risk_off_score: float
    sentiment_panic_threshold: float
    sentiment_low_coverage_count: int
    sentiment_full_coverage_count: int
    push_no_recommendation: bool
    push_heartbeat_enabled: bool
    push_heartbeat_interval_minutes: int
    push_dedup_enabled: bool
    push_dedup_summary_minutes: int
    push_price_change_threshold: float
    market_status_push_enabled: bool
    review_enabled: bool
    review_hour: int
    review_minute: int
    review_target_chars: int
    self_learning_enabled: bool
    self_learning_hour: int
    self_learning_minute: int
    self_learning_lookback_days: int
    self_learning_min_samples: int
    self_learning_max_step_pct: float
    self_learning_apply_changes: bool
    self_learning_notify: bool
    self_learning_cooldown_days: int
    self_learning_stable_min_win_rate: float
    self_learning_stable_min_avg_return_pct: float
    self_learning_stable_max_stop_rate: float
    self_learning_ai_review_enabled: bool
    self_learning_ai_min_confidence: float
    paper_trading_enabled: bool
    paper_initial_cash: float
    paper_account_path: Path
    paper_max_position_pct: float
    paper_slippage_pct: float
    paper_commission_rate: float
    paper_min_commission: float
    paper_stamp_duty_rate: float
    paper_lot_size: int
    paper_disciplined_execution_enabled: bool
    paper_policy_max_names: int
    paper_policy_max_total_exposure: float
    paper_policy_max_position_pct: float
    paper_policy_min_rebalance_delta_pct: float
    paper_policy_min_holding_days: int
    paper_policy_hard_stop_loss_pct: float
    paper_policy_trail_activation_pct: float
    paper_policy_trailing_stop_pct: float
    paper_policy_exit_panic_score: float
    primary_strategy_id: str
    hybrid_alpha_lock_parameters: bool
    multi_strategy_enabled: bool
    multi_strategy_mode: str
    multi_strategy_max_exposure: float
    multi_strategy_paper_enabled: bool
    market_regime_confirm_cycles: int
    market_regime_state_path: Path
    fundamental_cache_path: Path
    fundamental_cache_ttl_hours: int
    quality_growth_industries: list[str]
    backtest_default_days: int
    backtest_holding_days: int
    tushare_token: str | None
    tushare_base_url: str | None
    minimax_api_key: str | None
    minimax_base_url: str
    minimax_endpoint: str | None
    minimax_model: str
    minimax_fallback_model: str | None
    minimax_provider: str
    stock_ai_primary_api_key: str | None
    stock_ai_primary_endpoint: str | None
    stock_ai_primary_model: str
    stock_ai_secondary_api_key: str | None
    stock_ai_secondary_endpoint: str | None
    stock_ai_secondary_model: str
    stock_ai_tertiary_api_key: str | None
    stock_ai_tertiary_endpoint: str | None
    stock_ai_tertiary_model: str
    ai_degraded_fallback_enabled: bool
    ai_degraded_position_multiplier: float
    feishu_webhook_url: str | None
    feishu_enabled: bool
    send_in_dry_run: bool

    def ensure_dirs(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    storage_dir = Path(os.getenv("STORAGE_DIR", PROJECT_ROOT / "storage")).resolve()
    db_path = Path(os.getenv("SQLITE_DB_PATH", storage_dir / "db.sqlite")).resolve()
    settings = Settings(
        app_name=os.getenv("APP_NAME", ENGINE_NAME),
        engine_name=os.getenv("ENGINE_NAME", ENGINE_NAME),
        engine_positioning=os.getenv("ENGINE_POSITIONING", ENGINE_POSITIONING),
        value_statement=os.getenv("VALUE_STATEMENT", VALUE_STATEMENT),
        project_root=PROJECT_ROOT,
        storage_dir=storage_dir,
        db_path=db_path,
        loop_seconds=max(_int_env("RUN_INTERVAL", _int_env("LOOP_SECONDS", RUN_INTERVAL)), 5),
        run_on_start=_bool_env("RUN_ON_START", True),
        dry_run=_bool_env("DRY_RUN", True),
        akshare_enabled=_bool_env("AKSHARE_ENABLED", True),
        watch_symbols=_csv_env("WATCH_SYMBOLS"),
        market_sentiment_symbols=_csv_env("MARKET_SENTIMENT_SYMBOLS") or list(
            DEFAULT_MARKET_SENTIMENT_SYMBOLS
        ),
        stock_selection_universe_profile=_choice_env(
            "STOCK_SELECTION_UNIVERSE_PROFILE",
            "adaptive",
            {"watchlist", "settings", "current", "default", "large", "active_mid", "blended", "institutional", "adaptive"},
        ),
        stock_selection_universe_limit=max(_int_env("STOCK_SELECTION_UNIVERSE_LIMIT", 160), 5),
        stock_selection_universe_cache_path=Path(
            os.getenv("STOCK_SELECTION_UNIVERSE_CACHE_PATH")
            or storage_dir / "stock_selection_universe.json"
        ).resolve(),
        stock_selection_universe_cache_hours=min(
            max(_int_env("STOCK_SELECTION_UNIVERSE_CACHE_HOURS", 36), 1), 72
        ),
        max_candidates=max(_int_env("MAX_CANDIDATES", 20), 1),
        max_push_stocks=min(max(_int_env("MAX_PUSH_STOCKS", 3), 1), 10),
        min_turnover_yi=max(_float_env("MIN_TURNOVER_YI", 2.0), 0.0),
        confidence_threshold=min(max(_float_env("CONFIDENCE_THRESHOLD", 0.62), 0.0), 1.0),
        max_position_weight=min(max(_float_env("MAX_POSITION_WEIGHT", 0.12), 0.0), 1.0),
        max_recommend_pct_change=max(_float_env("MAX_RECOMMEND_PCT_CHANGE", 8.5), 0.0),
        near_limit_up_buffer=min(max(_float_env("NEAR_LIMIT_UP_BUFFER", 0.015), 0.0), 0.2),
        buy_range_pullback_pct=min(max(_float_env("BUY_RANGE_PULLBACK_PCT", 0.008), 0.0), 0.2),
        max_chase_pct=min(max(_float_env("MAX_CHASE_PCT", 0.012), 0.0), 0.2),
        stop_loss_pct=min(max(_float_env("STOP_LOSS_PCT", 0.035), 0.0), 0.2),
        min_quality_score=min(max(_float_env("MIN_QUALITY_SCORE", 55), 0.0), 100.0),
        sentiment_enabled=_bool_env("SENTIMENT_ENABLED", True),
        sentiment_min_buy_score=min(max(_float_env("SENTIMENT_MIN_BUY_SCORE", 53), 0.0), 100.0),
        sentiment_risk_off_score=min(max(_float_env("SENTIMENT_RISK_OFF_SCORE", 38), 0.0), 100.0),
        sentiment_panic_threshold=min(max(_float_env("SENTIMENT_PANIC_THRESHOLD", 72), 0.0), 100.0),
        sentiment_low_coverage_count=max(_int_env("SENTIMENT_LOW_COVERAGE_COUNT", 5), 1),
        sentiment_full_coverage_count=max(_int_env("SENTIMENT_FULL_COVERAGE_COUNT", 30), 1),
        push_no_recommendation=_bool_env("PUSH_NO_RECOMMENDATION", False),
        push_heartbeat_enabled=_bool_env("PUSH_HEARTBEAT_ENABLED", False),
        push_heartbeat_interval_minutes=max(_int_env("PUSH_HEARTBEAT_INTERVAL_MINUTES", 15), 5),
        push_dedup_enabled=_bool_env("PUSH_DEDUP_ENABLED", True),
        push_dedup_summary_minutes=max(_int_env("PUSH_DEDUP_SUMMARY_MINUTES", 60), 5),
        push_price_change_threshold=min(max(_float_env("PUSH_PRICE_CHANGE_THRESHOLD", 0.01), 0.0), 0.2),
        market_status_push_enabled=_bool_env("MARKET_STATUS_PUSH_ENABLED", True),
        review_enabled=_bool_env("REVIEW_ENABLED", False),
        review_hour=min(max(_int_env("REVIEW_HOUR", 20), 0), 23),
        review_minute=min(max(_int_env("REVIEW_MINUTE", 0), 0), 59),
        review_target_chars=max(_int_env("REVIEW_TARGET_CHARS", 1000), 500),
        self_learning_enabled=_bool_env("SELF_LEARNING_ENABLED", True),
        self_learning_hour=min(max(_int_env("SELF_LEARNING_HOUR", 20), 0), 23),
        self_learning_minute=min(max(_int_env("SELF_LEARNING_MINUTE", 30), 0), 59),
        self_learning_lookback_days=max(_int_env("SELF_LEARNING_LOOKBACK_DAYS", 30), 3),
        self_learning_min_samples=max(_int_env("SELF_LEARNING_MIN_SAMPLES", 30), 5),
        self_learning_max_step_pct=min(max(_float_env("SELF_LEARNING_MAX_STEP_PCT", 0.08), 0.01), 0.2),
        self_learning_apply_changes=_bool_env("SELF_LEARNING_APPLY_CHANGES", True),
        self_learning_notify=_bool_env("SELF_LEARNING_NOTIFY", True),
        self_learning_cooldown_days=max(_int_env("SELF_LEARNING_COOLDOWN_DAYS", 5), 0),
        self_learning_stable_min_win_rate=min(max(_float_env("SELF_LEARNING_STABLE_MIN_WIN_RATE", 0.55), 0.0), 1.0),
        self_learning_stable_min_avg_return_pct=_float_env("SELF_LEARNING_STABLE_MIN_AVG_RETURN_PCT", 0.2),
        self_learning_stable_max_stop_rate=min(max(_float_env("SELF_LEARNING_STABLE_MAX_STOP_RATE", 0.12), 0.0), 1.0),
        self_learning_ai_review_enabled=_bool_env("SELF_LEARNING_AI_REVIEW_ENABLED", True),
        self_learning_ai_min_confidence=min(max(_float_env("SELF_LEARNING_AI_MIN_CONFIDENCE", 0.65), 0.0), 1.0),
        paper_trading_enabled=_bool_env("PAPER_TRADING_ENABLED", False),
        paper_initial_cash=max(_float_env("PAPER_INITIAL_CASH", 1000000.0), 10000.0),
        paper_account_path=Path(
            os.getenv("PAPER_ACCOUNT_PATH") or storage_dir / "paper_account.json"
        ).resolve(),
        paper_max_position_pct=min(max(_float_env("PAPER_MAX_POSITION_PCT", 0.12), 0.01), 0.5),
        paper_slippage_pct=min(max(_float_env("PAPER_SLIPPAGE_PCT", 0.001), 0.0), 0.05),
        paper_commission_rate=min(max(_float_env("PAPER_COMMISSION_RATE", 0.00025), 0.0), 0.01),
        paper_min_commission=max(_float_env("PAPER_MIN_COMMISSION", 5.0), 0.0),
        paper_stamp_duty_rate=min(max(_float_env("PAPER_STAMP_DUTY_RATE", 0.0005), 0.0), 0.01),
        paper_lot_size=max(_int_env("PAPER_LOT_SIZE", 100), 1),
        paper_disciplined_execution_enabled=_bool_env("PAPER_DISCIPLINED_EXECUTION_ENABLED", True),
        paper_policy_max_names=min(max(_int_env("PAPER_POLICY_MAX_NAMES", 4), 1), 12),
        paper_policy_max_total_exposure=min(
            max(_float_env("PAPER_POLICY_MAX_TOTAL_EXPOSURE", 0.72), 0.05), 0.95
        ),
        paper_policy_max_position_pct=min(
            max(_float_env("PAPER_POLICY_MAX_POSITION_PCT", 0.12), 0.01), 0.5
        ),
        paper_policy_min_rebalance_delta_pct=min(
            max(_float_env("PAPER_POLICY_MIN_REBALANCE_DELTA_PCT", 0.02), 0.0), 0.2
        ),
        paper_policy_min_holding_days=max(_int_env("PAPER_POLICY_MIN_HOLDING_DAYS", 3), 1),
        paper_policy_hard_stop_loss_pct=min(
            max(_float_env("PAPER_POLICY_HARD_STOP_LOSS_PCT", 0.065), 0.01), 0.3
        ),
        paper_policy_trail_activation_pct=min(
            max(_float_env("PAPER_POLICY_TRAIL_ACTIVATION_PCT", 0.12), 0.01), 1.0
        ),
        paper_policy_trailing_stop_pct=min(
            max(_float_env("PAPER_POLICY_TRAILING_STOP_PCT", 0.085), 0.01), 0.3
        ),
        paper_policy_exit_panic_score=min(
            max(_float_env("PAPER_POLICY_EXIT_PANIC_SCORE", 64.0), 0.0), 100.0
        ),
        primary_strategy_id=_choice_env(
            "PRIMARY_STRATEGY_ID", "hybrid_alpha", {"hybrid_alpha", "multi_strategy"}
        ),
        hybrid_alpha_lock_parameters=_bool_env("HYBRID_ALPHA_LOCK_PARAMETERS", True),
        multi_strategy_enabled=_bool_env("MULTI_STRATEGY_ENABLED", True),
        multi_strategy_mode=_choice_env("MULTI_STRATEGY_MODE", "shadow", {"shadow", "active", "disabled"}),
        multi_strategy_max_exposure=min(max(_float_env("MULTI_STRATEGY_MAX_EXPOSURE", 0.85), 0.05), 0.95),
        multi_strategy_paper_enabled=_bool_env("MULTI_STRATEGY_PAPER_ENABLED", True),
        market_regime_confirm_cycles=max(_int_env("MARKET_REGIME_CONFIRM_CYCLES", 2), 1),
        market_regime_state_path=Path(
            os.getenv("MARKET_REGIME_STATE_PATH") or storage_dir / "market_regime_state.json"
        ).resolve(),
        fundamental_cache_path=Path(
            os.getenv("FUNDAMENTAL_CACHE_PATH") or storage_dir / "fundamental_cache.json"
        ).resolve(),
        fundamental_cache_ttl_hours=max(_int_env("FUNDAMENTAL_CACHE_TTL_HOURS", 24), 1),
        quality_growth_industries=_csv_env(
            "QUALITY_GROWTH_INDUSTRIES"
        )
        or ["半导体", "通信", "软件", "人工智能", "电力设备", "新能源", "高端制造", "医药"],
        backtest_default_days=max(_int_env("BACKTEST_DEFAULT_DAYS", 30), 1),
        backtest_holding_days=max(_int_env("BACKTEST_HOLDING_DAYS", 3), 1),
        tushare_token=os.getenv("TUSHARE_TOKEN") or TUSHARE_TOKEN or None,
        tushare_base_url=os.getenv("TUSHARE_BASE_URL") or None,
        minimax_api_key=os.getenv("MINIMAX_API_KEY") or MINIMAX_API_KEY or None,
        minimax_base_url=os.getenv("MINIMAX_BASE_URL") or MINIMAX_BASE_URL,
        minimax_endpoint=os.getenv("MINIMAX_ENDPOINT") or MINIMAX_ENDPOINT,
        minimax_model=_minimax_model(os.getenv("MINIMAX_MODEL")),
        minimax_fallback_model=_fallback_model(os.getenv("MINIMAX_FALLBACK_MODEL")),
        minimax_provider=os.getenv("MINIMAX_PROVIDER") or MINIMAX_PROVIDER,
        stock_ai_primary_api_key=(
            os.getenv("STOCK_AI_PRIMARY_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or None
        ),
        stock_ai_primary_endpoint=(
            os.getenv("STOCK_AI_PRIMARY_ENDPOINT")
            or os.getenv("DEEPSEEK_ENDPOINT")
            or STOCK_AI_PRIMARY_ENDPOINT
        ),
        stock_ai_primary_model=(
            os.getenv("STOCK_AI_PRIMARY_MODEL")
            or os.getenv("DEEPSEEK_MODEL")
            or STOCK_AI_PRIMARY_MODEL
        ),
        stock_ai_secondary_api_key=(
            os.getenv("STOCK_AI_SECONDARY_API_KEY") or os.getenv("MINIMAX_API_KEY") or None
        ),
        stock_ai_secondary_endpoint=(
            os.getenv("STOCK_AI_SECONDARY_ENDPOINT")
            or os.getenv("MINIMAX_ENDPOINT")
            or MINIMAX_ENDPOINT
        ),
        stock_ai_secondary_model=os.getenv("STOCK_AI_SECONDARY_MODEL") or STOCK_AI_SECONDARY_MODEL,
        stock_ai_tertiary_api_key=(
            os.getenv("STOCK_AI_TERTIARY_API_KEY") or os.getenv("MINIMAX_API_KEY") or None
        ),
        stock_ai_tertiary_endpoint=(
            os.getenv("STOCK_AI_TERTIARY_ENDPOINT")
            or os.getenv("MINIMAX_ENDPOINT")
            or MINIMAX_ENDPOINT
        ),
        stock_ai_tertiary_model=os.getenv("STOCK_AI_TERTIARY_MODEL") or STOCK_AI_TERTIARY_MODEL,
        ai_degraded_fallback_enabled=_bool_env("AI_DEGRADED_FALLBACK_ENABLED", True),
        ai_degraded_position_multiplier=min(
            max(_float_env("AI_DEGRADED_POSITION_MULTIPLIER", 1.0), 0.1), 1.0
        ),
        feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL") or os.getenv("FEISHU_WEBHOOK") or FEISHU_WEBHOOK or None,
        feishu_enabled=_bool_env("FEISHU_ENABLED", True),
        send_in_dry_run=_bool_env("SEND_IN_DRY_RUN", False),
    )
    settings.ensure_dirs()
    return settings


settings = load_settings()
