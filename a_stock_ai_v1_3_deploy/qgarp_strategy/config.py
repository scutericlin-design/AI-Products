from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


# These are deliberately few, economically distinct research hypotheses—not a
# large search grid.  Any candidate must still pass the pre-registered
# walk-forward and out-of-sample checks before paper trading is considered.
PROFILE_DEFAULTS: dict[str, dict[str, float]] = {
    "quality_value_momentum": {
        "quality": 0.30, "growth": 0.10, "value": 0.25, "momentum": 0.25, "low_vol": 0.10,
        "top_pct": 0.15, "min_roe": 5.0, "max_debt": 75.0,
    },
    "quality_value_defensive": {
        "quality": 0.35, "growth": 0.10, "value": 0.30, "momentum": 0.10, "low_vol": 0.15,
        "top_pct": 0.18, "min_roe": 6.0, "max_debt": 70.0,
    },
    "quality_momentum": {
        "quality": 0.30, "growth": 0.15, "value": 0.10, "momentum": 0.30, "low_vol": 0.15,
        "top_pct": 0.15, "min_roe": 6.0, "max_debt": 75.0,
    },
}


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _text(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


@dataclass(frozen=True)
class QGARPSettings:
    project_root: Path
    storage_dir: Path
    db_path: Path
    reports_dir: Path
    tushare_token: str | None
    tushare_base_url: str | None
    akshare_enabled: bool
    universe_limit: int
    universe_symbols: tuple[str, ...]
    min_listing_days: int
    min_average_amount_yuan: float
    financial_ttl_hours: int
    strategy_version: str
    strategy_profile: str
    min_history_days: int
    factor_quality_weight: float
    factor_growth_weight: float
    factor_value_weight: float
    factor_momentum_weight: float
    factor_low_vol_weight: float
    factor_liquidity_weight: float
    selection_top_pct: float
    factor_confirmation_count: int
    min_roe: float
    max_debt_to_assets: float
    require_positive_pe: bool
    require_positive_pb: bool
    momentum_short_days: int
    momentum_long_days: int
    momentum_skip_days: int
    weighting_mode: str
    rebalance_months: int
    max_names: int
    max_single_weight: float
    max_industry_weight: float
    max_theme_weight: float
    uptrend_exposure: float
    range_exposure: float
    risk_off_exposure: float
    stop_loss_pct: float
    take_profit_pct: float
    trailing_stop_pct: float
    daily_exit_enabled: bool
    catastrophic_stop_pct: float
    paper_enabled: bool
    paper_initial_cash: float
    paper_max_position_pct: float
    paper_slippage_pct: float
    paper_commission_rate: float
    paper_min_commission: float
    paper_stamp_duty_rate: float
    paper_lot_size: int
    push_enabled: bool
    feishu_webhook_url: str | None
    dry_run: bool
    benchmark_symbol: str
    backtest_index_code: str
    backtest_universe_source: str
    backtest_universe_limit: int
    backtest_min_adjustment_coverage: float
    ai_explanation_enabled: bool

    def ensure_dirs(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    @property
    def backtest_universe_id(self) -> str:
        return self.backtest_index_code if self.backtest_universe_source == "index_weight" else "QGARP_DAILY_BASIC_PIT"

    @property
    def factor_weights(self) -> dict[str, float]:
        """Normalized weights make a partially supplied env configuration safe."""
        raw = {
            "quality": self.factor_quality_weight,
            "growth": self.factor_growth_weight,
            "value": self.factor_value_weight,
            "momentum": self.factor_momentum_weight,
            "low_vol": self.factor_low_vol_weight,
            "liquidity": self.factor_liquidity_weight,
        }
        total = sum(max(value, 0.0) for value in raw.values())
        return {key: (max(value, 0.0) / total if total else 0.0) for key, value in raw.items()}

    def parameter_snapshot(self) -> dict[str, object]:
        return {
            "strategy_version": self.strategy_version,
            "strategy_profile": self.strategy_profile,
            "factor_weights": self.factor_weights,
            "selection_top_pct": self.selection_top_pct,
            "factor_confirmation_count": self.factor_confirmation_count,
            "quality_gates": {"min_roe": self.min_roe, "max_debt_to_assets": self.max_debt_to_assets},
            "valuation_gates": {"positive_pe": self.require_positive_pe, "positive_pb": self.require_positive_pb},
            "momentum": {"short_days": self.momentum_short_days, "long_days": self.momentum_long_days, "skip_days": self.momentum_skip_days},
            "portfolio": {
                "weighting_mode": self.weighting_mode,
                "rebalance_months": self.rebalance_months,
                "max_names": self.max_names,
                "max_single_weight": self.max_single_weight,
                "max_industry_weight": self.max_industry_weight,
            },
            "risk": {
                "uptrend_exposure": self.uptrend_exposure,
                "range_exposure": self.range_exposure,
                "risk_off_exposure": self.risk_off_exposure,
                "daily_exit_enabled": self.daily_exit_enabled,
                "catastrophic_stop_pct": self.catastrophic_stop_pct,
            },
            "data_integrity": {"min_adjustment_coverage": self.backtest_min_adjustment_coverage},
        }


def load_settings(*, ensure_dirs: bool = True) -> QGARPSettings:
    storage_dir = Path(os.getenv("QGARP_STORAGE_DIR") or PROJECT_ROOT / "qgarp_strategy" / "local_data").resolve()
    symbols = tuple(item.strip().upper() for item in os.getenv("QGARP_UNIVERSE_SYMBOLS", "").split(",") if item.strip())
    requested_profile = _text("QGARP_STRATEGY_PROFILE", "quality_value_momentum").lower()
    profile = requested_profile if requested_profile in PROFILE_DEFAULTS else "quality_value_momentum"
    profile_defaults = PROFILE_DEFAULTS[profile]
    settings = QGARPSettings(
        project_root=PROJECT_ROOT,
        storage_dir=storage_dir,
        db_path=Path(os.getenv("QGARP_DB_PATH") or storage_dir / "qgarp.sqlite").resolve(),
        reports_dir=Path(os.getenv("QGARP_REPORTS_DIR") or storage_dir / "reports").resolve(),
        tushare_token=os.getenv("QGARP_TUSHARE_TOKEN") or os.getenv("TUSHARE_TOKEN") or None,
        tushare_base_url=os.getenv("QGARP_TUSHARE_BASE_URL") or os.getenv("TUSHARE_BASE_URL") or None,
        akshare_enabled=_bool("QGARP_AKSHARE_ENABLED", True),
        universe_limit=max(_int("QGARP_UNIVERSE_LIMIT", 250), 20),
        universe_symbols=symbols,
        min_listing_days=max(_int("QGARP_MIN_LISTING_DAYS", 250), 60),
        min_average_amount_yuan=max(_float("QGARP_MIN_AVG_AMOUNT_YUAN", 80_000_000.0), 0.0),
        financial_ttl_hours=max(_int("QGARP_FINANCIAL_TTL_HOURS", 24), 1),
        strategy_version=_text("QGARP_STRATEGY_VERSION", "v2.0_quality_value_momentum_research"),
        strategy_profile=profile,
        min_history_days=max(_int("QGARP_MIN_HISTORY_DAYS", 130), 61),
        factor_quality_weight=max(_float("QGARP_FACTOR_QUALITY_WEIGHT", profile_defaults["quality"]), 0.0),
        factor_growth_weight=max(_float("QGARP_FACTOR_GROWTH_WEIGHT", profile_defaults["growth"]), 0.0),
        factor_value_weight=max(_float("QGARP_FACTOR_VALUE_WEIGHT", profile_defaults["value"]), 0.0),
        factor_momentum_weight=max(_float("QGARP_FACTOR_MOMENTUM_WEIGHT", profile_defaults["momentum"]), 0.0),
        factor_low_vol_weight=max(_float("QGARP_FACTOR_LOW_VOL_WEIGHT", profile_defaults["low_vol"]), 0.0),
        factor_liquidity_weight=max(_float("QGARP_FACTOR_LIQUIDITY_WEIGHT", 0.0), 0.0),
        selection_top_pct=min(max(_float("QGARP_SELECTION_TOP_PCT", profile_defaults["top_pct"]), 0.02), 0.50),
        factor_confirmation_count=min(max(_int("QGARP_FACTOR_CONFIRMATION_COUNT", 2), 0), 5),
        min_roe=_float("QGARP_MIN_ROE", profile_defaults["min_roe"]),
        max_debt_to_assets=min(max(_float("QGARP_MAX_DEBT_TO_ASSETS", profile_defaults["max_debt"]), 1.0), 100.0),
        require_positive_pe=_bool("QGARP_REQUIRE_POSITIVE_PE", True),
        require_positive_pb=_bool("QGARP_REQUIRE_POSITIVE_PB", True),
        momentum_short_days=min(max(_int("QGARP_MOMENTUM_SHORT_DAYS", 60), 21), 240),
        momentum_long_days=min(max(_int("QGARP_MOMENTUM_LONG_DAYS", 120), 42), 360),
        momentum_skip_days=min(max(_int("QGARP_MOMENTUM_SKIP_DAYS", 20), 0), 60),
        weighting_mode=_text("QGARP_WEIGHTING_MODE", "equal").lower(),
        rebalance_months=min(max(_int("QGARP_REBALANCE_MONTHS", 1), 1), 12),
        max_names=min(max(_int("QGARP_MAX_NAMES", 15), 5), 40),
        max_single_weight=min(max(_float("QGARP_MAX_SINGLE_WEIGHT", 0.08), 0.01), 0.20),
        max_industry_weight=min(max(_float("QGARP_MAX_INDUSTRY_WEIGHT", 0.25), 0.05), 0.60),
        max_theme_weight=min(max(_float("QGARP_MAX_THEME_WEIGHT", 0.15), 0.05), 0.50),
        uptrend_exposure=min(max(_float("QGARP_UPTREND_EXPOSURE", 0.95), 0.10), 1.0),
        range_exposure=min(max(_float("QGARP_RANGE_EXPOSURE", 0.72), 0.10), 1.0),
        risk_off_exposure=min(max(_float("QGARP_RISK_OFF_EXPOSURE", 0.40), 0.0), 1.0),
        stop_loss_pct=min(max(_float("QGARP_STOP_LOSS_PCT", 0.22), 0.01), 0.50),
        take_profit_pct=min(max(_float("QGARP_TAKE_PROFIT_PCT", 0.35), 0.02), 1.0),
        trailing_stop_pct=min(max(_float("QGARP_TRAILING_STOP_PCT", 0.20), 0.01), 0.50),
        daily_exit_enabled=_bool("QGARP_DAILY_EXIT_ENABLED", False),
        catastrophic_stop_pct=min(max(_float("QGARP_CATASTROPHIC_STOP_PCT", 0.22), 0.05), 0.50),
        # Q-GARP is locked to research-only while v4 style research is active.
        # Do not permit environment drift to turn an experiment into a paper run.
        paper_enabled=False,
        paper_initial_cash=max(_float("QGARP_PAPER_INITIAL_CASH", 1_000_000.0), 10_000.0),
        paper_max_position_pct=min(max(_float("QGARP_PAPER_MAX_POSITION_PCT", 0.07), 0.01), 0.20),
        paper_slippage_pct=min(max(_float("QGARP_PAPER_SLIPPAGE_PCT", 0.001), 0.0), 0.05),
        paper_commission_rate=min(max(_float("QGARP_PAPER_COMMISSION_RATE", 0.00025), 0.0), 0.01),
        paper_min_commission=max(_float("QGARP_PAPER_MIN_COMMISSION", 5.0), 0.0),
        paper_stamp_duty_rate=min(max(_float("QGARP_PAPER_STAMP_DUTY_RATE", 0.0005), 0.0), 0.01),
        paper_lot_size=max(_int("QGARP_PAPER_LOT_SIZE", 100), 1),
        push_enabled=False,
        feishu_webhook_url=os.getenv("QGARP_FEISHU_WEBHOOK") or os.getenv("FEISHU_WEBHOOK") or None,
        dry_run=True,
        benchmark_symbol=os.getenv("QGARP_BENCHMARK_SYMBOL", "000906.SH").upper(),
        backtest_index_code=os.getenv("QGARP_BACKTEST_INDEX_CODE", "000906.SH").upper(),
        backtest_universe_source=(os.getenv("QGARP_BACKTEST_UNIVERSE_SOURCE", "daily_basic_pit").strip().lower()),
        backtest_universe_limit=min(max(_int("QGARP_BACKTEST_UNIVERSE_LIMIT", 800), 100), 1200),
        backtest_min_adjustment_coverage=min(max(_float("QGARP_BACKTEST_MIN_ADJ_COVERAGE", 0.98), 0.0), 1.0),
        ai_explanation_enabled=_bool("QGARP_AI_EXPLANATION_ENABLED", False),
    )
    if ensure_dirs:
        settings.ensure_dirs()
    return settings
