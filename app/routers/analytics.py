from __future__ import annotations

import csv
import json
import math
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
from app.services.portfolio_advice import build_strategy_score_bundle, build_user_advice
from app.services.stock_lookup import lookup_stock_name, normalize_symbol
from app.services.timezone import beijing_iso


router = APIRouter(prefix="/api/analytics", tags=["analytics"])

DATA_HEALTH_PATH = PROJECT_ROOT / "data" / "processed" / "data_health_latest.json"
FACTOR_DIAGNOSTICS_PATH = PROJECT_ROOT / "data" / "processed" / "factor_diagnostics_latest.json"
RECOMMENDED_POOL_PATH = PROJECT_ROOT / "data" / "processed" / "recommended_pool.csv"
SCORED_UNIVERSE_PATH = PROJECT_ROOT / "data" / "processed" / "scored_universe_latest.csv"
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


def _find_symbol_row(rows: list[dict[str, str]], code: str) -> dict[str, str] | None:
    return next((row for row in rows if normalize_symbol(row.get("symbol", "")) == code), None)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _stock_level_reason(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    dropped_tokens = ("市场风格", "市场：", "缩量防御", "震荡上行", "高波动修复")
    parts = [
        item.strip()
        for item in text.split("；")
        if item.strip() and not any(token in item for token in dropped_tokens)
    ]
    return "；".join(parts)


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
        "requested_at": beijing_iso(item.requested_at),
        "decided_at": beijing_iso(item.decided_at),
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
    universe_rows = read_csv_rows(SCORED_UNIVERSE_PATH)
    signal_rows = read_csv_rows(SIGNAL_LATEST_PATH)
    pool = _find_symbol_row(pool_rows, code)
    universe = _find_symbol_row(universe_rows, code)
    signal = _find_symbol_row(signal_rows, code)
    strategy_scores = build_strategy_score_bundle({code}).get(code, {})
    position = db.scalar(
        select(PortfolioPosition).where(PortfolioPosition.user_id == user.id, PortfolioPosition.symbol == code)
    )
    advice_record = None
    if position:
        user_positions = db.scalars(
            select(PortfolioPosition).where(PortfolioPosition.user_id == user.id).order_by(PortfolioPosition.weight.desc())
        ).all()
        advice_record = next(
            (item for item in build_user_advice(user_positions) if normalize_symbol(str(item.get("symbol", ""))) == code),
            None,
        )
        if advice_record and advice_record.get("strategy_scores"):
            strategy_scores = advice_record["strategy_scores"]

    row = pool or universe or signal or {}
    score_source = "system_pool_top30" if pool else "scored_universe_latest" if universe else "legacy_signal_latest" if signal else "missing"
    if not row:
        primary_strategy = next(
            (
                item
                for item in [strategy_scores.get("short"), strategy_scores.get("mid_long")]
                if item and item.get("status") == "available"
            ),
            None,
        )
        if primary_strategy:
            row = primary_strategy
            score_source = f"{primary_strategy.get('strategy_key')}_strategy_cache"
    score = _safe_float(row.get("price_factor_score"))
    action = row.get("action") or "no_signal"
    report_record = dict(row)
    report_record["strategy_scores"] = strategy_scores
    report_record["signal_action"] = action
    report_record["weight"] = position.weight if position else 0.0
    report_record["cost_price"] = position.cost_price if position else None
    if position and row.get("close") and position.cost_price:
        latest = _safe_float(row.get("close"))
        report_record["pnl_pct"] = latest / position.cost_price - 1 if latest and position.cost_price else None
    if advice_record:
        finalized = advice_record
    else:
        from scripts.build_portfolio_advice import finalize_advice_record

        finalized = finalize_advice_record(report_record, max_single=0.12, watch_cap=0.04)
    holding_score = finalized.get("holding_score")
    portfolio_action = finalized.get("portfolio_action")
    risk_flags = [flag for flag in (row.get("risk_flags") or "").split("|") if flag]
    reasons = []
    if score:
        reasons.append(f"新增买入评分 {score:.1f}")
    if holding_score is not None:
        reasons.append(f"综合持有评分 {holding_score:.1f}")
    if row.get("trade_date"):
        reasons.append(f"评分日期 {row['trade_date']}")
    reasons.append(f"评分来源 {score_source}")
    if row.get("reason"):
        stock_reason = _stock_level_reason(row["reason"])
        if stock_reason:
            reasons.append(stock_reason)
    if position:
        reasons.append(f"当前账户仓位 {position.weight:.2%}")
    if finalized.get("action_rationale"):
        reasons.append(finalized["action_rationale"])

    thesis = "暂未形成强信号，适合作为观察标的。"
    if position:
        thesis = finalized.get("action_rationale") or "该股票已在账户持仓中，应按综合持有评分和风险预算管理。"
    elif action == "buy" and score >= 78:
        thesis = "当前具备强势、流动性和价格确认的组合信号，可进入核心观察或小仓试错清单。"
    elif action in {"watch", "hold_or_reduce"}:
        thesis = "当前信号未达到核心买入标准，应以观察为主，等待评分或风险条件改善。"
    elif action == "avoid":
        thesis = "当前模型不支持新增买入，应等待趋势、成交或基本面条件修复。"

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
        "portfolio_action": portfolio_action,
        "score": holding_score if position and holding_score is not None else score,
        "buy_score": score,
        "holding_score": holding_score,
        "target_weight_low": finalized.get("target_weight_low"),
        "target_weight_high": finalized.get("target_weight_high"),
        "suggested_target_weight": finalized.get("suggested_target_weight"),
        "upgrade_conditions": finalized.get("upgrade_conditions"),
        "action_rationale": finalized.get("action_rationale"),
        "position_role": finalized.get("position_role"),
        "position_profile_key": finalized.get("position_profile_key"),
        "position_profile_label": finalized.get("position_profile_label"),
        "target_position_count": finalized.get("target_position_count"),
        "account_value_estimate": finalized.get("account_value_estimate"),
        "profile_max_single": finalized.get("profile_max_single"),
        "retail_position_note": finalized.get("retail_position_note"),
        "buy_initial_weight": finalized.get("buy_initial_weight"),
        "buy_add_weight": finalized.get("buy_add_weight"),
        "buy_max_weight": finalized.get("buy_max_weight"),
        "stop_loss_pct": finalized.get("stop_loss_pct"),
        "risk_per_trade_pct": finalized.get("risk_per_trade_pct"),
        "trade_plan": finalized.get("trade_plan"),
        "score_source": score_source,
        "trade_date": row.get("trade_date"),
        "stock_profile": row.get("stock_profile") or finalized.get("stock_profile"),
        "stock_profile_label": row.get("stock_profile_label") or finalized.get("stock_profile_label"),
        "profile_adjust_note": row.get("profile_adjust_note") or finalized.get("profile_adjust_note"),
        "latest_close": _safe_float(row.get("close"), default=0.0) or None,
        "pct_change": _safe_float(row.get("pct_change"), default=0.0) or None,
        "turnover_rate": _safe_float(row.get("turnover_rate"), default=0.0) or None,
        "model_version": row.get("model_version") or "institutional_score_v7_profile_adaptive_tushare",
        "raw_institutional_score": _safe_float(row.get("raw_institutional_score"), default=0.0) or None,
        "gate_penalty_score": _safe_float(row.get("gate_penalty_score"), default=0.0) or None,
        "alpha_score": _safe_float(row.get("alpha_score"), default=0.0) or None,
        "fundamental_quality_score": _safe_float(row.get("fundamental_quality_score"), default=0.0) or None,
        "valuation_sanity_score": _safe_float(row.get("valuation_sanity_score"), default=0.0) or None,
        "liquidity_capacity_score": _safe_float(row.get("liquidity_capacity_score"), default=0.0) or None,
        "risk_control_score": _safe_float(row.get("risk_control_score"), default=0.0) or None,
        "confidence": confidence,
        "thesis": thesis,
        "evidence": reasons,
        "risks": risks,
        "invalidation": invalidation,
        "manual_checklist": checklist,
        "strategy_scores": strategy_scores,
        "position": {
            "weight": position.weight,
            "cost_price": position.cost_price,
            "shares": position.shares,
        }
        if position
        else None,
        "source": "rule_based_research_report",
    }
