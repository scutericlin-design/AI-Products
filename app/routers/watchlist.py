from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import current_user
from app.models import User, WatchlistItem
from app.schemas import WatchlistIn, WatchlistOut


router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistOut])
def list_watchlist(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(WatchlistItem).where(WatchlistItem.user_id == user.id)).all()


@router.post("", response_model=WatchlistOut)
def add_watchlist(payload: WatchlistIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    symbol = payload.symbol.strip().upper().zfill(6)
    item = db.scalar(
        select(WatchlistItem).where(WatchlistItem.user_id == user.id, WatchlistItem.symbol == symbol)
    )
    if item is None:
        item = WatchlistItem(user_id=user.id, symbol=symbol, name=payload.name)
        db.add(item)
    item.name = payload.name
    item.note = payload.note
    db.commit()
    db.refresh(item)
    return item
