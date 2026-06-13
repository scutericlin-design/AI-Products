from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.database import get_db
from app.deps import current_user
from app.models import PortfolioPosition, User
from app.services.stock_lookup import lookup_stock_name, normalize_symbol


router = APIRouter(prefix="/api/analytics", tags=["analytics"])

DATA_HEALTH_PATH = PROJECT_ROOT / "data" / "processed" / "data_health_latest.json"
FACTOR_DIAGNOSTICS_PATH = PROJECT_ROOT / "data" / "processed" / "factor_diagnostics_latest.json"
RECOMMENDED_POOL_PATH = PROJECT_ROOT / "data" / "processed" / "recommended_pool.csv"
SIGNAL_LATEST_PATH = PROJECT_ROOT / "data" / "processed" / "signal_latest.csv"


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def run_script(script_name: str, timeout: int = 120) -> None:
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / script_name)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "message": f"{script_name} failed",
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
        )


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@router.get("/data-health/latest")
def latest_data_health(user: User = Depends(current_user)):
    return read_json(DATA_HEALTH_PATH, {"status": "missing", "datasets": []})


@router.post("/data-health/run")
def run_data_health(user: User = Depends(current_user)):
    run_script("check_data_health.py")
    return latest_data_health(user)


@router.get("/factors/latest")
def latest_factor_diagnostics(user: User = Depends(current_user)):
    return read_json(FACTOR_DIAGNOSTICS_PATH, {"factor_summaries": [], "score_group_returns": []})


@router.post("/factors/run")
def run_factor_diagnostics(user: User = Depends(current_user)):
    run_script("build_factor_diagnostics.py")
    return latest_factor_diagnostics(user)


@router.get("/research-report/{symbol}")
def research_report(symbol: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    code = normalize_symbol(symbol)
    name, source = lookup_stock_name(code)
    pool_rows = read_csv_rows(RECOMMENDED_POOL_PATH)
    signal_rows = read_csv_rows(SIGNAL_LATEST_PATH)
    pool = next((row for row in pool_rows if normalize_symbol(row.get("symbol", "")) == code), None)
    signal = next((row for row in signal_rows if normalize_symbol(row.get("symbol", "")) == code), None)
    position = db.scalar(
        select(PortfolioPosition).where(PortfolioPosition.user_id == user.id, PortfolioPosition.symbol == code)
    )

    row = pool or signal or {}
    score = float(row.get("price_factor_score") or 0)
    action = row.get("action") or "no_signal"
    risk_flags = [flag for flag in (row.get("risk_flags") or "").split("|") if flag]
    reasons = []
    if score:
        reasons.append(f"模型评分 {score:.1f}")
    if row.get("reason"):
        reasons.append(row["reason"])
    if position:
        reasons.append(f"当前账户仓位 {position.weight:.2%}")

    thesis = "暂未形成强信号，适合作为观察标的。"
    if action == "buy" and score >= 78:
        thesis = "当前具备强势、流动性和价格确认的组合信号，可进入核心观察或小仓试错清单。"
    elif action in {"watch", "hold_or_reduce"}:
        thesis = "当前信号未达到核心买入标准，应以观察、持有或降风险为主。"
    elif action == "avoid":
        thesis = "当前模型不支持继续加仓，应优先检查退出或降仓条件。"

    risks = risk_flags or ["数据层未发现硬性风险标记，但仍需人工检查公告、涨跌停和板块拥挤度。"]
    invalidation = [
        "收盘价跌破 20 日均线且成交额无法修复",
        "放量下跌并跌破最近平台低点",
        "公告、业绩或监管信息推翻原有上涨假设",
        "同主题股票集体走弱导致组合相关性风险升高",
    ]
    checklist = [
        "检查是否涨停、停牌或盘口无法成交",
        "核验最近公告、业绩预告、减持和解禁信息",
        "确认行业/主题是否过度拥挤",
        "确认买入前的止损位和目标仓位上限",
    ]
    confidence = "high" if action == "buy" and score >= 82 and not risk_flags else "medium" if score >= 70 else "low"

    return {
        "symbol": code,
        "name": name,
        "name_source": source,
        "action": action,
        "score": score,
        "confidence": confidence,
        "thesis": thesis,
        "evidence": reasons,
        "risks": risks,
        "invalidation": invalidation,
        "manual_checklist": checklist,
        "position": {
            "weight": position.weight,
            "cost_price": position.cost_price,
            "shares": position.shares,
        }
        if position
        else None,
        "source": "rule_based_research_report",
    }
