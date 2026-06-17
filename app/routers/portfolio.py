from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_feature
from app.models import AdviceLog, PortfolioPosition, User
from app.routers.system_pool import MODEL_VERSION, get_or_create_config, serialize_config
from app.schemas import (
    AdviceLogIn,
    AdviceLogOut,
    PortfolioAdviceOut,
    PositionIn,
    PositionOut,
    RiskSummaryOut,
    StockLookupOut,
)
from app.services.audit import write_audit_log
from app.services.portfolio_advice import build_user_advice, summarize_risk
from app.services.score_refresh import ready_tushare_owner, run_tushare_pool_refresh
from app.services.stock_lookup import lookup_stock_name


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
            "refreshed_at": datetime.utcnow().isoformat(),
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
        "refreshed_at": datetime.utcnow().isoformat(),
        "stdout": stdout,
    }


@router.get("/risk", response_model=RiskSummaryOut)
def portfolio_risk(user: User = Depends(require_feature("portfolio")), db: Session = Depends(get_db)):
    advice_rows = build_user_advice(_user_positions(user, db))
    return summarize_risk(advice_rows)


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
        "created_at": log.created_at.isoformat(),
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
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]
