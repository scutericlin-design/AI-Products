from __future__ import annotations

import csv
import json
import subprocess
import sys
from io import StringIO
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.database import get_db
from app.deps import current_admin, current_user, require_feature
from app.models import DataCacheEntry, DataExportRequest, PortfolioPosition, User
from app.schemas import DataCachePruneIn, DataExportRequestIn
from app.services.audit import write_audit_log
from app.services.data_cache import cache_summary, prune_cache
from app.services.stock_lookup import lookup_stock_name, normalize_symbol


router = APIRouter(prefix="/api/analytics", tags=["analytics"])

DATA_HEALTH_PATH = PROJECT_ROOT / "data" / "processed" / "data_health_latest.json"
FACTOR_DIAGNOSTICS_PATH = PROJECT_ROOT / "data" / "processed" / "factor_diagnostics_latest.json"
RECOMMENDED_POOL_PATH = PROJECT_ROOT / "data" / "processed" / "recommended_pool.csv"
SIGNAL_LATEST_PATH = PROJECT_ROOT / "data" / "processed" / "signal_latest.csv"
EXPORTABLE_DATASETS = {"daily", "daily_basic", "fina_indicator"}


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


@router.get("/data-cache")
def data_cache(user: User = Depends(current_user)):
    return cache_summary()


@router.post("/data-cache/prune")
def prune_data_cache(payload: DataCachePruneIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not payload.dry_run and getattr(user, "role", "customer") != "admin":
        raise HTTPException(status_code=403, detail="只有 Admin 可以删除旧数据缓存")
    try:
        result = prune_cache(payload.dataset, payload.keep_recent_days, payload.dry_run)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit_log(
        db,
        user,
        "data_cache.prune_preview" if payload.dry_run else "data_cache.prune",
        payload.dataset,
        {
            "dataset": payload.dataset,
            "keep_recent_days": payload.keep_recent_days,
            "dry_run": payload.dry_run,
            "matched_items": result.get("matched_items"),
            "deleted_items": result.get("deleted_items"),
            "deleted_bytes": result.get("deleted_bytes"),
        },
    )
    return result


def _serialize_export(item: DataExportRequest) -> dict[str, Any]:
    return {
        "id": item.id,
        "dataset": item.dataset,
        "symbols": [symbol for symbol in (item.symbols_csv or "").split(",") if symbol],
        "start_date": item.start_date,
        "end_date": item.end_date,
        "status": item.status,
        "requested_at": item.requested_at.isoformat() if item.requested_at else None,
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        "decision_note": item.decision_note,
    }


def _normalize_export_symbols(symbols: list[str]) -> list[str]:
    normalized = []
    for symbol in symbols:
        code = "".join(char for char in str(symbol) if char.isdigit())
        if len(code) >= 6:
            normalized.append(code[-6:])
    return sorted(set(normalized))


@router.get("/data-export/requests")
def list_data_export_requests(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(DataExportRequest)
        .where(DataExportRequest.user_id == user.id)
        .order_by(DataExportRequest.requested_at.desc())
        .limit(100)
    ).all()
    return {"rows": [_serialize_export(item) for item in rows]}


@router.post("/data-export/requests")
def create_data_export_request(
    payload: DataExportRequestIn,
    user: User = Depends(require_feature("data_export")),
    db: Session = Depends(get_db),
):
    symbols = _normalize_export_symbols(payload.symbols)
    item = DataExportRequest(
        user_id=user.id,
        dataset=payload.dataset,
        symbols_csv=",".join(symbols),
        start_date=payload.start_date,
        end_date=payload.end_date,
        status="pending",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    write_audit_log(
        db,
        user,
        "data_export.request",
        payload.dataset,
        {"request_id": item.id, "symbols": symbols, "start_date": payload.start_date, "end_date": payload.end_date},
    )
    return _serialize_export(item)


def _load_export_frame(item: DataExportRequest, db: Session) -> pd.DataFrame:
    import pandas as pd

    if item.dataset not in EXPORTABLE_DATASETS:
        raise HTTPException(status_code=400, detail="不支持该数据集导出")
    symbols = set(_normalize_export_symbols((item.symbols_csv or "").split(",")))
    query = select(DataCacheEntry).where(
        DataCacheEntry.provider == "tushare",
        DataCacheEntry.dataset == item.dataset,
    )
    if item.dataset in {"daily", "daily_basic"}:
        if item.start_date:
            query = query.where(DataCacheEntry.trade_date >= item.start_date)
        if item.end_date:
            query = query.where(DataCacheEntry.trade_date <= item.end_date)
    entries = db.scalars(query.order_by(DataCacheEntry.trade_date.asc(), DataCacheEntry.cache_key.asc())).all()
    frames: list[pd.DataFrame] = []
    for entry in entries:
        if not entry.payload_csv:
            continue
        frame = pd.read_csv(StringIO(entry.payload_csv), dtype={"ts_code": str, "trade_date": str, "end_date": str})
        if frame.empty:
            continue
        if symbols and "ts_code" in frame.columns:
            frame = frame[frame["ts_code"].astype(str).str.split(".").str[0].str.zfill(6).isin(symbols)]
        date_column = "trade_date" if "trade_date" in frame.columns else "end_date" if "end_date" in frame.columns else None
        if date_column:
            if item.start_date:
                frame = frame[frame[date_column].astype(str) >= item.start_date]
            if item.end_date:
                frame = frame[frame[date_column].astype(str) <= item.end_date]
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


@router.get("/data-export/requests/{request_id}/download")
def download_data_export(request_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(DataExportRequest, request_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="导出申请不存在")
    if item.status != "approved":
        raise HTTPException(status_code=403, detail="导出申请尚未被 Admin 批准")
    frame = _load_export_frame(item, db)
    buffer = StringIO()
    frame.to_csv(buffer, index=False, encoding="utf-8-sig")
    buffer.seek(0)
    filename = f"tushare_{item.dataset}_export_{item.id}.csv"
    write_audit_log(
        db,
        user,
        "data_export.download",
        str(item.id),
        {"dataset": item.dataset, "rows": len(frame), "symbols": item.symbols_csv},
    )
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
