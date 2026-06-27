from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_feature, user_feature_flags
from app.models import AdviceLog, PortfolioPosition, TradePlanReview, User
from app.routers.system_pool import MODEL_VERSION, get_or_create_config, serialize_config
from app.schemas import (
    AdviceLogIn,
    AdviceLogOut,
    ManualTradePlanIn,
    PortfolioAdviceOut,
    PositionIn,
    PositionOut,
    RiskSummaryOut,
    StockLookupOut,
    TradePlanReviewCreateIn,
    TradePlanReviewOut,
    TradePlanReviewUpdateIn,
)
from app.services.audit import write_audit_log
from app.services.portfolio_advice import (
    OPTIMAL_TRADE_PLAN_VERSION,
    build_manual_trade_plan,
    build_user_advice,
    summarize_risk,
)
from app.services.score_refresh import ready_tushare_owner, run_tushare_pool_refresh
from app.services.stock_lookup import lookup_stock_name
from app.services.timezone import beijing_iso, now_beijing_iso


router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().zfill(6)


@router.get("/positions", response_model=list[PositionOut])
def list_positions(user: User = Depends(require_feature("portfolio")), db: Session = Depends(get_db)):
    return db.scalars(
        select(PortfolioPosition).where(PortfolioPosition.user_id == user.id).order_by(PortfolioPosition.weight.desc())
    ).all()


def _user_positions(user: User, db: Session) -> list[PortfolioPosition]:
    return db.scalars(
        select(PortfolioPosition).where(PortfolioPosition.user_id == user.id).order_by(PortfolioPosition.weight.desc())
    ).all()


@router.get("/advice", response_model=list[PortfolioAdviceOut])
def portfolio_advice(user: User = Depends(require_feature("portfolio")), db: Session = Depends(get_db)):
    return build_user_advice(_user_positions(user, db))


@router.post("/advice/refresh")
def refresh_portfolio_advice(user: User = Depends(require_feature("portfolio")), db: Session = Depends(get_db)):
    positions = _user_positions(user, db)
    if not positions:
        return {
            "ok": True,
            "refreshed": False,
            "message": "暂无持仓，不需要刷新评分。",
            "rows": [],
            "risk": summarize_risk([]),
            "refreshed_at": now_beijing_iso(),
        }
    config = get_or_create_config(user, db)
    credential_owner = ready_tushare_owner(user, db)
    refreshed = False
    engine = "cached_scores"
    message = "已使用最新缓存评分生成个人持股建议。"
    stdout = ""
    if credential_owner is not None:
        refresh_result = run_tushare_pool_refresh(config, credential_owner)
        stdout = refresh_result.stdout
        if not refresh_result.ok:
            message = "TuShare 刷新失败，已回退到现有评分文件。"
            write_audit_log(
                db,
                user,
                "portfolio.score_refresh_failed",
                MODEL_VERSION,
                {
                    "credential_owner": credential_owner.email,
                    "stdout": refresh_result.stdout[-1200:],
                    "stderr": refresh_result.stderr[-1200:],
                },
            )
        else:
            refreshed = True
            engine = "tushare_pro"
            message = "已复用刚刚完成的 TuShare Pro 刷新结果，生成个人持股建议。" if refresh_result.reused_recent_result else "已调用 TuShare Pro 更新最新行情、评分和个人持股建议。"
            write_audit_log(
                db,
                user,
                "portfolio.score_refresh",
                MODEL_VERSION,
                {
                    "credential_owner": credential_owner.email,
                    "position_count": len(positions),
                    "reused_recent_result": refresh_result.reused_recent_result,
                    "config": serialize_config(config),
                },
            )
    else:
        message = "未找到可用 TuShare Pro 数据源，已使用现有评分文件生成建议。"

    rows = build_user_advice(positions)
    return {
        "ok": True,
        "refreshed": refreshed,
        "engine": engine,
        "model_version": MODEL_VERSION,
        "message": message,
        "rows": rows,
        "risk": summarize_risk(rows),
        "refreshed_at": now_beijing_iso(),
        "stdout": stdout,
    }


@router.get("/risk", response_model=RiskSummaryOut)
def portfolio_risk(user: User = Depends(require_feature("portfolio")), db: Session = Depends(get_db)):
    advice_rows = build_user_advice(_user_positions(user, db))
    return summarize_risk(advice_rows)


def require_optimal_trade_plan(user: User = Depends(require_feature("portfolio"))) -> User:
    if not user_feature_flags(user).get("optimal_trade_plan", False):
        raise HTTPException(status_code=403, detail="最优交易计划功能未开通，请联系 Admin 开启")
    return user


@router.post("/manual-trade-plan")
def manual_trade_plan(
    payload: ManualTradePlanIn,
    user: User = Depends(require_optimal_trade_plan),
    db: Session = Depends(get_db),
):
    positions = _user_positions(user, db)
    plan = build_manual_trade_plan(
        positions,
        account_value=payload.account_value,
        available_cash=payload.available_cash,
        min_trade_amount=payload.min_trade_amount,
        lot_size=payload.lot_size,
    )
    write_audit_log(
        db,
        user,
        "portfolio.manual_trade_plan",
        OPTIMAL_TRADE_PLAN_VERSION,
        {
            "account_value": payload.account_value,
            "available_cash": payload.available_cash,
            "position_count": plan.get("position_count"),
            "executable_count": plan.get("executable_count"),
            "target_position_count": plan.get("target_position_count"),
            "target_exposure": plan.get("target_exposure"),
        },
    )
    return plan


def _serialize_trade_plan_review(item: TradePlanReview) -> dict:
    snapshot = None
    if item.plan_snapshot_json:
        try:
            snapshot = json.loads(item.plan_snapshot_json)
        except json.JSONDecodeError:
            snapshot = None
    return {
        "id": item.id,
        "symbol": item.symbol,
        "name": item.name,
        "plan_action": item.plan_action,
        "planned_quantity": item.planned_quantity,
        "ideal_quantity": item.ideal_quantity,
        "target_weight": item.target_weight,
        "entry_timing_score": item.entry_timing_score,
        "odds_ratio": item.odds_ratio,
        "decision": item.decision,
        "actual_action": item.actual_action,
        "actual_return_pct": item.actual_return_pct,
        "review_note": item.review_note,
        "status": item.status,
        "created_at": beijing_iso(item.created_at),
        "reviewed_at": beijing_iso(item.reviewed_at),
        "plan_snapshot": snapshot,
    }


@router.post("/trade-plan-reviews", response_model=TradePlanReviewOut)
def create_trade_plan_review(
    payload: TradePlanReviewCreateIn,
    user: User = Depends(require_optimal_trade_plan),
    db: Session = Depends(get_db),
):
    item = TradePlanReview(
        user_id=user.id,
        symbol=normalize_symbol(payload.symbol),
        name=payload.name,
        plan_action=payload.plan_action,
        planned_quantity=payload.planned_quantity,
        ideal_quantity=payload.ideal_quantity,
        target_weight=payload.target_weight,
        entry_timing_score=payload.entry_timing_score,
        odds_ratio=payload.odds_ratio,
        decision=payload.decision,
        plan_snapshot_json=json.dumps(payload.plan_snapshot or {}, ensure_ascii=False, sort_keys=True),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    write_audit_log(
        db,
        user,
        "portfolio.trade_plan_review_create",
        item.symbol,
        {"decision": item.decision, "plan_action": item.plan_action, "target_weight": item.target_weight},
    )
    return _serialize_trade_plan_review(item)


@router.get("/trade-plan-reviews", response_model=list[TradePlanReviewOut])
def list_trade_plan_reviews(
    user: User = Depends(require_optimal_trade_plan),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(TradePlanReview)
        .where(TradePlanReview.user_id == user.id)
        .order_by(TradePlanReview.created_at.desc(), TradePlanReview.id.desc())
        .limit(200)
    ).all()
    return [_serialize_trade_plan_review(item) for item in rows]


@router.put("/trade-plan-reviews/{review_id}", response_model=TradePlanReviewOut)
def update_trade_plan_review(
    review_id: int,
    payload: TradePlanReviewUpdateIn,
    user: User = Depends(require_optimal_trade_plan),
    db: Session = Depends(get_db),
):
    item = db.scalar(select(TradePlanReview).where(TradePlanReview.id == review_id, TradePlanReview.user_id == user.id))
    if item is None:
        raise HTTPException(status_code=404, detail="复盘记录不存在")
    item.actual_action = payload.actual_action
    item.actual_return_pct = payload.actual_return_pct
    item.review_note = payload.review_note
    item.status = payload.status
    item.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    write_audit_log(
        db,
        user,
        "portfolio.trade_plan_review_update",
        item.symbol,
        {"status": item.status, "actual_return_pct": item.actual_return_pct},
    )
    return _serialize_trade_plan_review(item)


@router.get("/trade-plan-learning")
def trade_plan_learning(
    user: User = Depends(require_optimal_trade_plan),
    db: Session = Depends(get_db),
):
    rows = db.scalars(select(TradePlanReview).where(TradePlanReview.user_id == user.id)).all()
    reviewed = [row for row in rows if row.actual_return_pct is not None]
    accepted = [row for row in rows if row.decision == "accepted"]
    watched = [row for row in rows if row.decision == "watch"]
    wins = [row for row in reviewed if (row.actual_return_pct or 0) > 0]
    avg_return = sum(float(row.actual_return_pct or 0) for row in reviewed) / len(reviewed) if reviewed else None
    best_decision = None
    by_decision: dict[str, list[TradePlanReview]] = {}
    for row in reviewed:
        by_decision.setdefault(row.decision, []).append(row)
    decision_stats = []
    for decision, items in by_decision.items():
        decision_stats.append(
            {
                "decision": decision,
                "count": len(items),
                "win_rate": round(sum(1 for item in items if (item.actual_return_pct or 0) > 0) / len(items), 4),
                "avg_return_pct": round(sum(float(item.actual_return_pct or 0) for item in items) / len(items), 4),
            }
        )
    if decision_stats:
        best_decision = max(decision_stats, key=lambda item: item["avg_return_pct"])["decision"]
    insights = []
    if reviewed:
        insights.append(f"已复盘 {len(reviewed)} 条计划，胜率 {len(wins) / len(reviewed):.0%}。")
    else:
        insights.append("尚未形成足够复盘样本，先记录采纳/观察/跳过及5/10/20日表现。")
    if best_decision:
        insights.append(f"当前样本里表现最好的决策类型是 {best_decision}。")
    if len(watched) > len(accepted) * 2 and len(rows) >= 5:
        insights.append("观察样本偏多，可重点比较影子组合与真实交易的机会成本。")
    return {
        "total": len(rows),
        "reviewed": len(reviewed),
        "accepted": len(accepted),
        "watched": len(watched),
        "win_rate": round(len(wins) / len(reviewed), 4) if reviewed else None,
        "avg_return_pct": round(avg_return, 4) if avg_return is not None else None,
        "decision_stats": decision_stats,
        "insights": insights,
    }


@router.get("/lookup/{symbol}", response_model=StockLookupOut)
def lookup_symbol(symbol: str, user: User = Depends(require_feature("portfolio"))):
    normalized = normalize_symbol(symbol)
    name, source = lookup_stock_name(normalized)
    return {"symbol": normalized, "name": name, "source": source}


@router.post("/positions", response_model=PositionOut)
def upsert_position(
    payload: PositionIn,
    user: User = Depends(require_feature("portfolio")),
    db: Session = Depends(get_db),
):
    symbol = normalize_symbol(payload.symbol)
    name = (payload.name or "").strip()
    if not name:
        name, _source = lookup_stock_name(symbol)
    position = db.scalar(
        select(PortfolioPosition).where(
            PortfolioPosition.user_id == user.id,
            PortfolioPosition.symbol == symbol,
        )
    )
    if position is None:
        position = PortfolioPosition(user_id=user.id, symbol=symbol, name=name)
        db.add(position)

    position.name = name
    position.weight = payload.weight
    position.cost_price = payload.cost_price
    position.shares = payload.shares
    db.commit()
    db.refresh(position)
    write_audit_log(
        db,
        user,
        "portfolio.position_upsert",
        symbol,
        {"name": name, "weight": position.weight, "cost_price": position.cost_price, "shares": position.shares},
    )
    return position


@router.delete("/positions/{symbol}")
def delete_position(symbol: str, user: User = Depends(require_feature("portfolio")), db: Session = Depends(get_db)):
    position = db.scalar(
        select(PortfolioPosition).where(
            PortfolioPosition.user_id == user.id,
            PortfolioPosition.symbol == normalize_symbol(symbol),
        )
    )
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    normalized = position.symbol
    db.delete(position)
    db.commit()
    write_audit_log(db, user, "portfolio.position_delete", normalized, {"symbol": normalized})
    return {"ok": True}


@router.post("/advice-log", response_model=AdviceLogOut)
def accept_advice(payload: AdviceLogIn, user: User = Depends(require_feature("portfolio")), db: Session = Depends(get_db)):
    log = AdviceLog(
        user_id=user.id,
        symbol=normalize_symbol(payload.symbol),
        name=payload.name,
        action=payload.action,
        target_weight=payload.target_weight,
        reason=payload.reason,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    write_audit_log(
        db,
        user,
        "portfolio.advice_accept",
        log.symbol,
        {"name": log.name, "action": log.action, "target_weight": log.target_weight},
    )
    return {
        "id": log.id,
        "symbol": log.symbol,
        "name": log.name,
        "action": log.action,
        "target_weight": log.target_weight,
        "reason": log.reason,
        "status": log.status,
        "created_at": beijing_iso(log.created_at),
    }


@router.get("/advice-log", response_model=list[AdviceLogOut])
def list_advice_logs(user: User = Depends(require_feature("portfolio")), db: Session = Depends(get_db)):
    logs = db.scalars(
        select(AdviceLog).where(AdviceLog.user_id == user.id).order_by(AdviceLog.created_at.desc()).limit(100)
    ).all()
    return [
        {
            "id": log.id,
            "symbol": log.symbol,
            "name": log.name,
            "action": log.action,
            "target_weight": log.target_weight,
            "reason": log.reason,
            "status": log.status,
            "created_at": beijing_iso(log.created_at),
        }
        for log in logs
    ]
