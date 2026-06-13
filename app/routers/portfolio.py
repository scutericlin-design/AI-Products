from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import current_user
from app.models import PortfolioPosition, User
from app.schemas import PositionIn, PositionOut


router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().zfill(6)


@router.get("/positions", response_model=list[PositionOut])
def list_positions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(PortfolioPosition).where(PortfolioPosition.user_id == user.id).order_by(PortfolioPosition.weight.desc())
    ).all()


@router.post("/positions", response_model=PositionOut)
def upsert_position(
    payload: PositionIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    symbol = normalize_symbol(payload.symbol)
    position = db.scalar(
        select(PortfolioPosition).where(
            PortfolioPosition.user_id == user.id,
            PortfolioPosition.symbol == symbol,
        )
    )
    if position is None:
        position = PortfolioPosition(user_id=user.id, symbol=symbol, name=payload.name)
        db.add(position)

    position.name = payload.name
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
