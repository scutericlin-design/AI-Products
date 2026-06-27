from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


ENGINE_NAME = "A股市场实时感知引擎 v1.7"
ENGINE_POSITIONING = "market_realtime_perception_engine"
VALUE_STATEMENT = "v1.7真正价值不是追逐每一次波动，而是用组合管理让好股票有时间发挥"

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")

MINIMAX_BASE_URL = "https://api.cg-czk.top/v1"
MINIMAX_ENDPOINT = f"{MINIMAX_BASE_URL.rstrip('/')}/chat/completions"
MINIMAX_MODEL = "minimaxai/minimax-m2.7"
MINIMAX_PROVIDER = "custom"
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")

FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK") or os.getenv("FEISHU_WEBHOOK_URL", "")

RUN_INTERVAL = 300


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
    backtest_default_days: int
    backtest_holding_days: int
    tushare_token: str | None
    tushare_base_url: str | None
    minimax_api_key: str | None
    minimax_base_url: str
    minimax_endpoint: str | None
    minimax_model: str
    minimax_provider: str
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
        push_heartbeat_enabled=_bool_env("PUSH_HEARTBEAT_ENABLED", True),
        push_heartbeat_interval_minutes=max(_int_env("PUSH_HEARTBEAT_INTERVAL_MINUTES", 15), 5),
        push_dedup_enabled=_bool_env("PUSH_DEDUP_ENABLED", True),
        push_dedup_summary_minutes=max(_int_env("PUSH_DEDUP_SUMMARY_MINUTES", 60), 5),
        push_price_change_threshold=min(max(_float_env("PUSH_PRICE_CHANGE_THRESHOLD", 0.01), 0.0), 0.2),
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
        backtest_default_days=max(_int_env("BACKTEST_DEFAULT_DAYS", 30), 1),
        backtest_holding_days=max(_int_env("BACKTEST_HOLDING_DAYS", 3), 1),
        tushare_token=os.getenv("TUSHARE_TOKEN") or TUSHARE_TOKEN or None,
        tushare_base_url=os.getenv("TUSHARE_BASE_URL") or None,
        minimax_api_key=os.getenv("MINIMAX_API_KEY") or MINIMAX_API_KEY or None,
        minimax_base_url=os.getenv("MINIMAX_BASE_URL") or MINIMAX_BASE_URL,
        minimax_endpoint=os.getenv("MINIMAX_ENDPOINT") or MINIMAX_ENDPOINT,
        minimax_model=os.getenv("MINIMAX_MODEL") or MINIMAX_MODEL,
        minimax_provider=os.getenv("MINIMAX_PROVIDER") or MINIMAX_PROVIDER,
        feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL") or os.getenv("FEISHU_WEBHOOK") or FEISHU_WEBHOOK or None,
        feishu_enabled=_bool_env("FEISHU_ENABLED", True),
        send_in_dry_run=_bool_env("SEND_IN_DRY_RUN", False),
    )
    settings.ensure_dirs()
    return settings


settings = load_settings()
