from __future__ import annotations

import json
import subprocess
import sys

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import PROJECT_ROOT
from app.deps import current_user
from app.models import User


router = APIRouter(prefix="/api/backtest", tags=["backtest"])

BACKTEST_JSON_PATH = PROJECT_ROOT / "data" / "processed" / "backtest_latest.json"
BACKTEST_TRADES_PATH = PROJECT_ROOT / "data" / "processed" / "backtest_trades.csv"
PORTFOLIO_BACKTEST_JSON_PATH = PROJECT_ROOT / "data" / "processed" / "portfolio_backtest_latest.json"
STRATEGY_OPTIMIZATION_JSON_PATH = PROJECT_ROOT / "data" / "processed" / "strategy_optimization_latest.json"


def read_latest() -> dict:
    if not BACKTEST_JSON_PATH.exists():
        return {"metrics": None}
    return json.loads(BACKTEST_JSON_PATH.read_text(encoding="utf-8"))


def read_portfolio_latest() -> dict:
    if not PORTFOLIO_BACKTEST_JSON_PATH.exists():
        return {"metrics": None}
    return json.loads(PORTFOLIO_BACKTEST_JSON_PATH.read_text(encoding="utf-8"))


def read_optimization_latest() -> dict:
    if not STRATEGY_OPTIMIZATION_JSON_PATH.exists():
        return {"profiles": []}
    return json.loads(STRATEGY_OPTIMIZATION_JSON_PATH.read_text(encoding="utf-8"))


@router.get("/latest")
def latest_backtest(user: User = Depends(current_user)):
    return read_latest()


@router.get("/portfolio/latest")
def latest_portfolio_backtest(user: User = Depends(current_user)):
    return read_portfolio_latest()


@router.get("/optimization/latest")
def latest_strategy_optimization(user: User = Depends(current_user)):
    return read_optimization_latest()


@router.post("/run")
def run_backtest(
    hold_days: int = Query(default=20, ge=5, le=60),
    min_score: float = Query(default=78, ge=0, le=100),
    user: User = Depends(current_user),
):
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "build_backtest.py"),
        "--hold-days",
        str(hold_days),
        "--min-score",
        str(min_score),
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
    return read_latest()


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
    user: User = Depends(current_user),
):
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "build_portfolio_backtest.py"),
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
    ]
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
    return read_portfolio_latest()


@router.post("/optimization/run")
def run_strategy_optimization(user: User = Depends(current_user)):
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
    return read_optimization_latest()
