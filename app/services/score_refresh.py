from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.models import DataSourceConfig, StrategyConfig, User


TUSHARE_READY_STATUSES = {"available", "configured_manual_check"}
DEFAULT_STRATEGY_TYPE = "short_elastic_2_8w"
STRATEGY_REBUILD_PRESETS = {
    "short_elastic_2_8w": {"lookback_trade_days": "90", "finance_limit": "800"},
    "mid_long_quality_3_12m": {"lookback_trade_days": "180", "finance_limit": "800"},
}
_REFRESH_LOCK = threading.Lock()
_LAST_REFRESH_BY_KEY: dict[tuple, tuple[float, "PoolRefreshResult"]] = {}


@dataclass(frozen=True)
class PoolRefreshResult:
    returncode: int
    stdout: str
    stderr: str
    reused_recent_result: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def ready_tushare_owner(user: User, db: Session) -> User | None:
    own_config = db.scalar(
        select(DataSourceConfig).where(
            DataSourceConfig.user_id == user.id,
            DataSourceConfig.provider == "tushare",
            DataSourceConfig.status.in_(TUSHARE_READY_STATUSES),
            DataSourceConfig.api_token_cipher.is_not(None),
        )
    )
    if own_config is not None:
        return user

    config = db.scalar(
        select(DataSourceConfig)
        .where(
            DataSourceConfig.provider == "tushare",
            DataSourceConfig.status.in_(TUSHARE_READY_STATUSES),
            DataSourceConfig.api_token_cipher.is_not(None),
        )
        .order_by(DataSourceConfig.priority.asc(), DataSourceConfig.user_id.asc())
    )
    return db.get(User, config.user_id) if config is not None else None


def build_tushare_pool_refresh_command(config: StrategyConfig, credential_owner: User) -> list[str]:
    strategy_type = getattr(config, "strategy_type", None) or DEFAULT_STRATEGY_TYPE
    preset = STRATEGY_REBUILD_PRESETS.get(strategy_type, STRATEGY_REBUILD_PRESETS[DEFAULT_STRATEGY_TYPE])
    return [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "build_tushare_institutional_pool.py"),
        "--email",
        credential_owner.email,
        "--strategy-type",
        strategy_type,
        "--limit",
        str(config.pool_limit),
        "--lookback-trade-days",
        preset["lookback_trade_days"],
        "--finance-limit",
        preset["finance_limit"],
        "--min-amount-yi",
        str(config.min_amount_yi),
        "--buy-score-threshold",
        str(config.buy_score_threshold),
        "--min-pct-change",
        str(config.min_pct_change),
        "--max-pct-change",
        str(config.max_pct_change),
        "--min-close-position-pct",
        str(config.min_close_position_pct),
        "--max-amplitude-pct",
        str(config.max_amplitude_pct),
        "--target-weight",
        str(config.target_weight),
        "--markets",
        config.market_scope or "main,chinext,star",
        "--sleep",
        "0.03",
    ]


def _refresh_key(config: StrategyConfig, credential_owner: User) -> tuple:
    return (
        credential_owner.email,
        config.pool_limit,
        config.min_amount_yi,
        config.buy_score_threshold,
        config.min_pct_change,
        config.max_pct_change,
        config.min_close_position_pct,
        config.max_amplitude_pct,
        config.target_weight,
        getattr(config, "strategy_type", None) or DEFAULT_STRATEGY_TYPE,
        config.market_scope or "main,chinext,star",
    )


def _tail(value: object, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[-limit:]
    return str(value)[-limit:]


def run_tushare_pool_refresh(
    config: StrategyConfig,
    credential_owner: User,
    *,
    timeout: int = 900,
    reuse_window_seconds: int = 60,
) -> PoolRefreshResult:
    key = _refresh_key(config, credential_owner)
    with _REFRESH_LOCK:
        cached = _LAST_REFRESH_BY_KEY.get(key)
        if cached is not None:
            refreshed_at, result = cached
            if time.monotonic() - refreshed_at <= reuse_window_seconds and result.ok:
                return PoolRefreshResult(
                    returncode=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    reused_recent_result=True,
                )

        command = build_tushare_pool_refresh_command(config, credential_owner)
        try:
            completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            return PoolRefreshResult(
                returncode=124,
                stdout=_tail(exc.stdout),
                stderr=_tail(exc.stderr) or f"TuShare refresh timed out after {timeout} seconds.",
            )
        except Exception as exc:
            return PoolRefreshResult(returncode=1, stdout="", stderr=_tail(exc))
        result = PoolRefreshResult(
            returncode=completed.returncode,
            stdout=_tail(completed.stdout),
            stderr=_tail(completed.stderr),
        )
        if result.ok:
            _LAST_REFRESH_BY_KEY[key] = (time.monotonic(), result)
        return result
