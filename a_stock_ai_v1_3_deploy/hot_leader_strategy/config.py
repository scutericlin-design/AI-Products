from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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


@dataclass(frozen=True)
class HotLeaderSettings:
    storage_dir: Path
    db_path: Path
    reports_dir: Path
    tushare_token: str | None
    tushare_base_url: str | None
    akshare_enabled: bool
    strategy_version: str
    universe_limit: int
    min_listing_days: int
    min_amount_yuan: float
    min_theme_members: int
    min_hot_themes: int
    max_names: int
    max_single_weight: float
    max_total_exposure: float
    max_theme_exposure: float
    trend_entry_min_return: float
    pullback_min_return: float
    pullback_max_drawdown: float
    entry_max_daily_gain: float
    stop_loss_pct: float
    take_profit_pct: float
    trailing_stop_pct: float
    max_holding_days: int
    auto_enabled: bool
    auto_refresh_calendar_days: int
    intraday_enabled: bool
    intraday_entry_enabled: bool
    intraday_interval_minutes: int
    intraday_entry_min_change: float
    intraday_entry_max_change: float
    intraday_near_limit_buffer: float
    intraday_buy_cutoff: str
    intraday_event_cooldown_minutes: int
    paper_enabled: bool
    push_enabled: bool
    dry_run: bool
    paper_initial_cash: float
    paper_slippage_pct: float
    paper_commission_rate: float
    paper_min_commission: float
    paper_stamp_duty_rate: float
    paper_lot_size: int
    feishu_webhook_url: str | None

    def ensure_dirs(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def parameter_snapshot(self) -> dict[str, object]:
        return {
            "version": self.strategy_version,
            "theme": {"min_members": self.min_theme_members, "min_hot_themes": self.min_hot_themes},
            "entry": {"trend_min_5d_return": self.trend_entry_min_return, "pullback_min_10d_return": self.pullback_min_return, "pullback_max_drawdown": self.pullback_max_drawdown, "max_daily_gain": self.entry_max_daily_gain},
            "portfolio": {"max_names": self.max_names, "max_single_weight": self.max_single_weight, "max_total_exposure": self.max_total_exposure, "max_theme_exposure": self.max_theme_exposure},
            "risk": {"stop_loss_pct": self.stop_loss_pct, "take_profit_pct": self.take_profit_pct, "trailing_stop_pct": self.trailing_stop_pct, "max_holding_days": self.max_holding_days},
            "automation": {
                "enabled": self.auto_enabled,
                "refresh_calendar_days": self.auto_refresh_calendar_days,
                "close_plan_next_open_execution": True,
                "intraday_monitor": {
                    "enabled": self.intraday_enabled,
                    "entry_enabled": self.intraday_entry_enabled,
                    "interval_minutes": self.intraday_interval_minutes,
                    "entry_change_range": [self.intraday_entry_min_change, self.intraday_entry_max_change],
                    "near_limit_buffer": self.intraday_near_limit_buffer,
                    "buy_cutoff": self.intraday_buy_cutoff,
                    "event_cooldown_minutes": self.intraday_event_cooldown_minutes,
                },
            },
            "mode": "independent_local_paper_only",
        }


def load_settings(*, ensure_dirs: bool = True) -> HotLeaderSettings:
    storage_dir = Path(os.getenv("HOT_LEADER_STORAGE_DIR") or ROOT / "hot_leader_strategy" / "local_data").resolve()
    settings = HotLeaderSettings(
        storage_dir=storage_dir,
        db_path=Path(os.getenv("HOT_LEADER_DB_PATH") or storage_dir / "hot_leader.sqlite").resolve(),
        reports_dir=Path(os.getenv("HOT_LEADER_REPORTS_DIR") or storage_dir / "reports").resolve(),
        tushare_token=os.getenv("HOT_LEADER_TUSHARE_TOKEN") or os.getenv("TUSHARE_TOKEN") or None,
        tushare_base_url=os.getenv("HOT_LEADER_TUSHARE_BASE_URL") or os.getenv("TUSHARE_BASE_URL") or None,
        akshare_enabled=_bool("HOT_LEADER_AKSHARE_ENABLED", True),
        strategy_version=os.getenv("HOT_LEADER_STRATEGY_VERSION", "v1.0_theme_leader_research"),
        universe_limit=min(max(_int("HOT_LEADER_UNIVERSE_LIMIT", 5500), 100), 6000),
        min_listing_days=max(_int("HOT_LEADER_MIN_LISTING_DAYS", 250), 60),
        min_amount_yuan=max(_float("HOT_LEADER_MIN_AMOUNT_YUAN", 150_000_000), 0),
        min_theme_members=max(_int("HOT_LEADER_MIN_THEME_MEMBERS", 3), 2),
        min_hot_themes=max(_int("HOT_LEADER_MIN_HOT_THEMES", 2), 1),
        max_names=min(max(_int("HOT_LEADER_MAX_NAMES", 3), 1), 8),
        max_single_weight=min(max(_float("HOT_LEADER_MAX_SINGLE_WEIGHT", 0.05), 0.01), 0.12),
        max_total_exposure=min(max(_float("HOT_LEADER_MAX_TOTAL_EXPOSURE", 0.15), 0.01), 0.35),
        max_theme_exposure=min(max(_float("HOT_LEADER_MAX_THEME_EXPOSURE", 0.10), 0.01), 0.35),
        trend_entry_min_return=min(max(_float("HOT_LEADER_TREND_ENTRY_MIN_RETURN", 0.12), 0.03), 0.60),
        pullback_min_return=min(max(_float("HOT_LEADER_PULLBACK_MIN_RETURN", 0.15), 0.05), 0.80),
        pullback_max_drawdown=min(max(_float("HOT_LEADER_PULLBACK_MAX_DRAWDOWN", 0.10), 0.02), 0.25),
        entry_max_daily_gain=min(max(_float("HOT_LEADER_ENTRY_MAX_DAILY_GAIN", 0.075), 0.01), 0.19),
        stop_loss_pct=min(max(_float("HOT_LEADER_STOP_LOSS_PCT", 0.08), 0.02), 0.20),
        take_profit_pct=min(max(_float("HOT_LEADER_TAKE_PROFIT_PCT", 0.18), 0.04), 0.60),
        trailing_stop_pct=min(max(_float("HOT_LEADER_TRAILING_STOP_PCT", 0.07), 0.02), 0.20),
        max_holding_days=min(max(_int("HOT_LEADER_MAX_HOLDING_DAYS", 7), 2), 20),
        auto_enabled=_bool("HOT_LEADER_AUTO_ENABLED", False),
        auto_refresh_calendar_days=min(max(_int("HOT_LEADER_AUTO_REFRESH_CALENDAR_DAYS", 75), 35), 160),
        intraday_enabled=_bool("HOT_LEADER_INTRADAY_ENABLED", False),
        intraday_entry_enabled=_bool("HOT_LEADER_INTRADAY_ENTRY_ENABLED", True),
        intraday_interval_minutes=min(max(_int("HOT_LEADER_INTRADAY_INTERVAL_MINUTES", 2), 1), 15),
        intraday_entry_min_change=min(max(_float("HOT_LEADER_INTRADAY_ENTRY_MIN_CHANGE", -0.02), -0.10), 0.10),
        intraday_entry_max_change=min(max(_float("HOT_LEADER_INTRADAY_ENTRY_MAX_CHANGE", 0.045), 0.005), 0.19),
        intraday_near_limit_buffer=min(max(_float("HOT_LEADER_INTRADAY_NEAR_LIMIT_BUFFER", 0.015), 0.005), 0.05),
        intraday_buy_cutoff=os.getenv("HOT_LEADER_INTRADAY_BUY_CUTOFF", "14:45"),
        intraday_event_cooldown_minutes=min(max(_int("HOT_LEADER_INTRADAY_EVENT_COOLDOWN_MINUTES", 120), 15), 480),
        paper_enabled=_bool("HOT_LEADER_PAPER_ENABLED", False),
        push_enabled=_bool("HOT_LEADER_PUSH_ENABLED", False),
        dry_run=_bool("HOT_LEADER_DRY_RUN", True),
        paper_initial_cash=max(_float("HOT_LEADER_PAPER_INITIAL_CASH", 1_000_000), 10_000),
        paper_slippage_pct=min(max(_float("HOT_LEADER_PAPER_SLIPPAGE_PCT", 0.0015), 0), 0.05),
        paper_commission_rate=min(max(_float("HOT_LEADER_PAPER_COMMISSION_RATE", 0.00025), 0), 0.01),
        paper_min_commission=max(_float("HOT_LEADER_PAPER_MIN_COMMISSION", 5), 0),
        paper_stamp_duty_rate=min(max(_float("HOT_LEADER_PAPER_STAMP_DUTY_RATE", 0.0005), 0), 0.01),
        paper_lot_size=max(_int("HOT_LEADER_PAPER_LOT_SIZE", 100), 1),
        feishu_webhook_url=os.getenv("HOT_LEADER_FEISHU_WEBHOOK") or None,
    )
    if ensure_dirs:
        settings.ensure_dirs()
    return settings
