from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_MINIMAX_MODEL = "stepfun-ai/step-3.7-flash"
DEFAULT_MINIMAX_FALLBACK_MODEL = "minimaxai/minimax-m3"


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    try:
        return float(value) if value not in {None, ""} else default
    except ValueError:
        return default


def _minimax_model(value: str | None) -> str:
    """Keep StepFun as the ETF reviewer's primary model."""
    model = (value or DEFAULT_MINIMAX_MODEL).strip()
    return DEFAULT_MINIMAX_MODEL if model.startswith("minimaxai/") else model


def _fallback_model(value: str | None) -> str | None:
    model = (value or DEFAULT_MINIMAX_FALLBACK_MODEL).strip()
    return model or None


@dataclass(frozen=True)
class ETFStrategySettings:
    project_root: Path
    storage_dir: Path
    db_path: Path
    reports_dir: Path
    tushare_token: str | None
    tushare_base_url: str | None
    minimax_api_key: str | None
    minimax_endpoint: str | None
    minimax_model: str
    ai_enabled: bool
    request_pause_seconds: float
    daily_amount_multiplier: float
    min_avg_turnover_yuan: float
    turnover_to_order_multiple: float
    max_nav_premium_pct: float
    minimax_fallback_model: str | None = DEFAULT_MINIMAX_FALLBACK_MODEL
    minute_enabled: bool = False
    minute_paper_trading_enabled: bool = True
    minute_initial_cash: float = 1_000_000.0
    minute_max_position_weight: float = 0.70
    minute_slippage_rate: float = 0.0005
    minute_commission_rate: float = 0.0001
    minute_min_commission: float = 5.0
    minute_lot_size: int = 100
    minute_max_stale_seconds: int = 180
    minute_stop_loss_pct: float = 0.08
    minute_akshare_fallback_enabled: bool = True
    minute_batch_spot_enabled: bool = True
    minute_batch_spot_min_coverage: float = 0.90
    # Kept as a default for direct construction in legacy tests and scripts.
    # Runtime configuration defaults to the source-file implementation below.
    minute_strategy_profile: str = "local_research"

    def ensure_dirs(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> ETFStrategySettings:
    storage_dir = Path(
        os.getenv("ETF_STRATEGY_STORAGE_DIR") or PROJECT_ROOT / "etf_strategy" / "local_data"
    ).resolve()
    reports_dir = Path(
        os.getenv("ETF_STRATEGY_REPORTS_DIR") or storage_dir / "reports"
    ).resolve()
    settings = ETFStrategySettings(
        project_root=PROJECT_ROOT,
        storage_dir=storage_dir,
        db_path=Path(os.getenv("ETF_STRATEGY_DB_PATH") or storage_dir / "etf_strategy.sqlite").resolve(),
        reports_dir=reports_dir,
        tushare_token=os.getenv("ETF_TUSHARE_TOKEN") or os.getenv("TUSHARE_TOKEN") or None,
        tushare_base_url=os.getenv("ETF_TUSHARE_BASE_URL") or os.getenv("TUSHARE_BASE_URL") or None,
        minimax_api_key=os.getenv("ETF_MINIMAX_API_KEY") or os.getenv("MINIMAX_API_KEY") or None,
        minimax_endpoint=(
            os.getenv("ETF_MINIMAX_ENDPOINT")
            or os.getenv("MINIMAX_ENDPOINT")
            or "https://api.cg-czk.top/v1/chat/completions"
        ),
        minimax_model=_minimax_model(os.getenv("ETF_MINIMAX_MODEL") or os.getenv("MINIMAX_MODEL")),
        ai_enabled=_bool_env("ETF_MINIMAX_ENABLED", False),
        request_pause_seconds=max(_float_env("ETF_TUSHARE_REQUEST_PAUSE_SECONDS", 0.08), 0.0),
        # TuShare daily amount is stored in thousand yuan; keep conversion explicit and configurable.
        daily_amount_multiplier=max(_float_env("ETF_DAILY_AMOUNT_MULTIPLIER", 1000.0), 1.0),
        min_avg_turnover_yuan=max(_float_env("ETF_MIN_AVG_TURNOVER_YUAN", 20_000_000.0), 0.0),
        turnover_to_order_multiple=max(_float_env("ETF_TURNOVER_TO_ORDER_MULTIPLE", 20.0), 1.0),
        max_nav_premium_pct=min(max(_float_env("ETF_MAX_NAV_PREMIUM_PCT", 0.03), 0.0), 0.3),
        minimax_fallback_model=_fallback_model(
            os.getenv("ETF_MINIMAX_FALLBACK_MODEL") or os.getenv("MINIMAX_FALLBACK_MODEL")
        ),
        minute_enabled=_bool_env("ETF_MINUTE_ENABLED", False),
        minute_paper_trading_enabled=_bool_env("ETF_MINUTE_PAPER_TRADING_ENABLED", True),
        minute_initial_cash=max(_float_env("ETF_MINUTE_INITIAL_CASH", 1_000_000.0), 10_000.0),
        minute_max_position_weight=min(max(_float_env("ETF_MINUTE_MAX_POSITION_WEIGHT", 0.70), 0.05), 0.95),
        minute_slippage_rate=min(max(_float_env("ETF_MINUTE_SLIPPAGE_RATE", 0.0005), 0.0), 0.05),
        minute_commission_rate=min(max(_float_env("ETF_MINUTE_COMMISSION_RATE", 0.0001), 0.0), 0.01),
        minute_min_commission=max(_float_env("ETF_MINUTE_MIN_COMMISSION", 5.0), 0.0),
        minute_lot_size=max(int(_float_env("ETF_MINUTE_LOT_SIZE", 100)), 1),
        minute_max_stale_seconds=max(int(_float_env("ETF_MINUTE_MAX_STALE_SECONDS", 180)), 30),
        minute_stop_loss_pct=min(max(_float_env("ETF_MINUTE_STOP_LOSS_PCT", 0.08), 0.01), 0.30),
        minute_akshare_fallback_enabled=_bool_env("ETF_MINUTE_AKSHARE_FALLBACK_ENABLED", True),
        minute_batch_spot_enabled=_bool_env("ETF_MINUTE_BATCH_SPOT_ENABLED", True),
        minute_batch_spot_min_coverage=min(max(_float_env("ETF_MINUTE_BATCH_SPOT_MIN_COVERAGE", 0.90), 0.50), 1.0),
        minute_strategy_profile=(os.getenv("ETF_MINUTE_STRATEGY_PROFILE") or "wufu_v7_static").strip().lower(),
    )
    settings.ensure_dirs()
    return settings
