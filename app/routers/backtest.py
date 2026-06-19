from __future__ import annotations

import json
import subprocess
import sys

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.database import get_db
from app.deps import require_feature
from app.models import User
from app.services.audit import write_audit_log


router = APIRouter(prefix="/api/backtest", tags=["backtest"])

BACKTEST_JSON_PATH = PROJECT_ROOT / "data" / "processed" / "backtest_latest.json"
BACKTEST_TRADES_PATH = PROJECT_ROOT / "data" / "processed" / "backtest_trades.csv"
PORTFOLIO_BACKTEST_JSON_PATH = PROJECT_ROOT / "data" / "processed" / "portfolio_backtest_latest.json"
STRATEGY_OPTIMIZATION_JSON_PATH = PROJECT_ROOT / "data" / "processed" / "strategy_optimization_latest.json"
WALK_FORWARD_JSON_PATH = PROJECT_ROOT / "data" / "processed" / "walk_forward_latest.json"
SIGNAL_DAILY_PATH = PROJECT_ROOT / "data" / "processed" / "signal_daily.csv"

STRATEGY_BACKTEST_SETTINGS = {
    "short": {
        "label": "短期策略",
        "signal_path": SIGNAL_DAILY_PATH,
        "event_json": PROJECT_ROOT / "data" / "processed" / "backtest_short_latest.json",
        "event_trades": PROJECT_ROOT / "data" / "processed" / "backtest_trades_short.csv",
        "portfolio_json": PROJECT_ROOT / "data" / "processed" / "portfolio_backtest_short_latest.json",
        "portfolio_curve": PROJECT_ROOT / "data" / "processed" / "portfolio_backtest_curve_short.csv",
        "portfolio_rebalances": PROJECT_ROOT / "data" / "processed" / "portfolio_backtest_rebalances_short.csv",
    },
    "mid_long": {
        "label": "长期策略",
        "signal_path": SIGNAL_DAILY_PATH,
        "event_json": PROJECT_ROOT / "data" / "processed" / "backtest_mid_long_latest.json",
        "event_trades": PROJECT_ROOT / "data" / "processed" / "backtest_trades_mid_long.csv",
        "portfolio_json": PROJECT_ROOT / "data" / "processed" / "portfolio_backtest_mid_long_latest.json",
        "portfolio_curve": PROJECT_ROOT / "data" / "processed" / "portfolio_backtest_curve_mid_long.csv",
        "portfolio_rebalances": PROJECT_ROOT / "data" / "processed" / "portfolio_backtest_rebalances_mid_long.csv",
    },
}


def strategy_settings(strategy_key: str) -> dict:
    try:
        return STRATEGY_BACKTEST_SETTINGS[strategy_key]
    except KeyError as exc:
        raise HTTPException(status_code=400, detail="Unsupported strategy_key") from exc


def attach_strategy(payload: dict, strategy_key: str) -> dict:
    settings = strategy_settings(strategy_key)
    payload = dict(payload)
    payload["strategy_key"] = strategy_key
    payload["strategy_label"] = settings["label"]
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        metrics.setdefault("strategy_key", strategy_key)
        metrics.setdefault("strategy_label", settings["label"])
    return payload


def read_latest(strategy_key: str = "short") -> dict:
    settings = strategy_settings(strategy_key)
    path = settings["event_json"]
    if strategy_key == "short" and not path.exists():
        path = BACKTEST_JSON_PATH
    if not path.exists():
        return attach_strategy({"metrics": None}, strategy_key)
    return attach_strategy(json.loads(path.read_text(encoding="utf-8")), strategy_key)


def read_portfolio_latest(strategy_key: str = "short") -> dict:
    settings = strategy_settings(strategy_key)
    path = settings["portfolio_json"]
    if strategy_key == "short" and not path.exists():
        path = PORTFOLIO_BACKTEST_JSON_PATH
    if not path.exists():
        return attach_strategy({"metrics": None}, strategy_key)
    return attach_strategy(json.loads(path.read_text(encoding="utf-8")), strategy_key)


def read_optimization_latest() -> dict:
    if not STRATEGY_OPTIMIZATION_JSON_PATH.exists():
        return {"profiles": []}
    return json.loads(STRATEGY_OPTIMIZATION_JSON_PATH.read_text(encoding="utf-8"))


def read_walk_forward_latest() -> dict:
    if not WALK_FORWARD_JSON_PATH.exists():
        return {"summary": None, "windows": []}
    return json.loads(WALK_FORWARD_JSON_PATH.read_text(encoding="utf-8"))


@router.get("/latest")
def latest_backtest(
    strategy_key: str = Query(default="short", pattern="^(short|mid_long)$"),
    user: User = Depends(require_feature("backtest")),
):
    return read_latest(strategy_key)


@router.get("/portfolio/latest")
def latest_portfolio_backtest(
    strategy_key: str = Query(default="short", pattern="^(short|mid_long)$"),
    user: User = Depends(require_feature("backtest")),
):
    return read_portfolio_latest(strategy_key)


@router.get("/optimization/latest")
def latest_strategy_optimization(user: User = Depends(require_feature("backtest"))):
    return read_optimization_latest()


@router.get("/walk-forward/latest")
def latest_walk_forward(user: User = Depends(require_feature("backtest"))):
    return read_walk_forward_latest()


@router.post("/run")
def run_backtest(
    hold_days: int = Query(default=20, ge=5, le=60),
    min_score: float = Query(default=78, ge=0, le=100),
    strategy_key: str = Query(default="short", pattern="^(short|mid_long)$"),
    user: User = Depends(require_feature("backtest")),
    db: Session = Depends(get_db),
):
    settings = strategy_settings(strategy_key)
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "build_backtest.py"),
        "--signals",
        str(settings["signal_path"]),
        "--hold-days",
        str(hold_days),
        "--min-score",
        str(min_score),
        "--output-json",
        str(settings["event_json"]),
        "--output-trades",
        str(settings["event_trades"]),
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=120)
    if completed.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Backtest failed",
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
        )
    write_audit_log(
        db,
        user,
        "backtest.event_run",
        strategy_key,
        {"hold_days": hold_days, "min_score": min_score, "strategy_key": strategy_key},
    )
    return read_latest(strategy_key)


@router.post("/portfolio/run")
def run_portfolio_backtest(
    top_n: int = Query(default=10, ge=3, le=30),
    min_score: float = Query(default=78, ge=0, le=100),
    rebalance_days: int = Query(default=5, ge=1, le=40),
    max_position: float = Query(default=0.10, ge=0.01, le=0.30),
    target_exposure: float = Query(default=0.60, ge=0.01, le=1.0),
    actions: str = Query(default="buy"),
    fee_bps: float = Query(default=5.0, ge=0, le=100),
    slippage_bps: float = Query(default=10.0, ge=0, le=200),
    enforce_trading_rules: bool = Query(default=True),
    stamp_tax_bps: float = Query(default=5.0, ge=0, le=100),
    limit_buffer_pct: float = Query(default=0.003, ge=0, le=0.02),
    strategy_key: str = Query(default="short", pattern="^(short|mid_long)$"),
    user: User = Depends(require_feature("backtest")),
    db: Session = Depends(get_db),
):
    settings = strategy_settings(strategy_key)
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "build_portfolio_backtest.py"),
        "--signals",
        str(settings["signal_path"]),
        "--top-n",
        str(top_n),
        "--min-score",
        str(min_score),
        "--rebalance-days",
        str(rebalance_days),
        "--max-position",
        str(max_position),
        "--target-exposure",
        str(target_exposure),
        "--actions",
        actions,
        "--fee-bps",
        str(fee_bps),
        "--slippage-bps",
        str(slippage_bps),
        "--stamp-tax-bps",
        str(stamp_tax_bps),
        "--limit-buffer-pct",
        str(limit_buffer_pct),
        "--output-json",
        str(settings["portfolio_json"]),
        "--output-curve",
        str(settings["portfolio_curve"]),
        "--output-rebalances",
        str(settings["portfolio_rebalances"]),
    ]
    if enforce_trading_rules:
        command.append("--enforce-trading-rules")
    completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=120)
    if completed.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Portfolio backtest failed",
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
        )
    write_audit_log(
        db,
        user,
        "backtest.portfolio_run",
        strategy_key,
        {
            "top_n": top_n,
            "min_score": min_score,
            "rebalance_days": rebalance_days,
            "max_position": max_position,
            "target_exposure": target_exposure,
            "strategy_key": strategy_key,
        },
    )
    return read_portfolio_latest(strategy_key)


@router.post("/optimization/run")
def run_strategy_optimization(user: User = Depends(require_feature("backtest")), db: Session = Depends(get_db)):
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "optimize_strategy_profiles.py"),
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=180)
    if completed.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Strategy optimization failed",
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
        )
    write_audit_log(db, user, "backtest.optimization_run", "strategy_profiles", {})
    return read_optimization_latest()


@router.post("/walk-forward/run")
def run_walk_forward(
    profile: str = Query(default="balanced", pattern="^(conservative|balanced|aggressive)$"),
    train_days: int = Query(default=120, ge=60, le=360),
    test_days: int = Query(default=40, ge=20, le=120),
    step_days: int = Query(default=40, ge=10, le=120),
    user: User = Depends(require_feature("backtest")),
    db: Session = Depends(get_db),
):
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "build_walk_forward.py"),
        "--profile",
        profile,
        "--train-days",
        str(train_days),
        "--test-days",
        str(test_days),
        "--step-days",
        str(step_days),
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=240)
    if completed.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Walk-forward validation failed",
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
        )
    write_audit_log(
        db,
        user,
        "backtest.walk_forward_run",
        profile,
        {"profile": profile, "train_days": train_days, "test_days": test_days, "step_days": step_days},
    )
    return read_walk_forward_latest()
