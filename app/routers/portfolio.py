from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import current_user
from app.models import AdviceLog, PortfolioPosition, User
from app.schemas import (
    AdviceLogIn,
    AdviceLogOut,
    PortfolioAdviceOut,
    PositionIn,
    PositionOut,
    RiskSummaryOut,
    StockLookupOut,
)
from app.services.portfolio_advice import build_user_advice, summarize_risk
from app.services.stock_lookup import lookup_stock_name


router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().zfill(6)


@router.get("/positions", response_model=list[PositionOut])
def list_positions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(PortfolioPosition).where(PortfolioPosition.user_id == user.id).order_by(PortfolioPosition.weight.desc())
    ).all()


def _user_positions(user: User, db: Session) -> list[PortfolioPosition]:
    return db.scalars(
        select(PortfolioPosition).where(PortfolioPosition.user_id == user.id).order_by(PortfolioPosition.weight.desc())
    ).all()


@router.get("/advice", response_model=list[PortfolioAdviceOut])
def portfolio_advice(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return build_user_advice(_user_positions(user, db))


@router.get("/risk", response_model=RiskSummaryOut)
def portfolio_risk(user: User = Depends(current_user), db: Session = Depends(get_db)):
    advice_rows = build_user_advice(_user_positions(user, db))
    return summarize_risk(advice_rows)


@router.get("/lookup/{symbol}", response_model=StockLookupOut)
def lookup_symbol(symbol: str, user: User = Depends(current_user)):
    normalized = normalize_symbol(symbol)
    name, source = lookup_stock_name(normalized)
    return {"symbol": normalized, "name": name, "source": source}


@router.post("/positions", response_model=PositionOut)
def upsert_position(
    payload: PositionIn,
    user: User = Depends(current_user),
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
    return position


@router.delete("/positions/{symbol}")
def delete_position(symbol: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    position = db.scalar(
        select(PortfolioPosition).where(
            PortfolioPosition.user_id == user.id,
            PortfolioPosition.symbol == normalize_symbol(symbol),
        )
    )
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    db.delete(position)
    db.commit()
    return {"ok": True}


@router.post("/advice-log", response_model=AdviceLogOut)
def accept_advice(payload: AdviceLogIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
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
def list_advice_logs(user: User = Depends(current_user), db: Session = Depends(get_db)):
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
