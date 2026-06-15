from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_feature
from app.models import User, WatchlistItem
from app.schemas import WatchlistIn, WatchlistOut
from app.services.audit import write_audit_log


router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistOut])
def list_watchlist(user: User = Depends(require_feature("portfolio")), db: Session = Depends(get_db)):
    return db.scalars(select(WatchlistItem).where(WatchlistItem.user_id == user.id)).all()


@router.post("", response_model=WatchlistOut)
def add_watchlist(payload: WatchlistIn, user: User = Depends(require_feature("portfolio")), db: Session = Depends(get_db)):
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
    write_audit_log(
        db,
        user,
        "watchlist.upsert",
        symbol,
        {"name": item.name, "note": item.note},
    )
    return item


@router.delete("/{symbol}")
def delete_watchlist(symbol: str, user: User = Depends(require_feature("portfolio")), db: Session = Depends(get_db)):
    normalized = symbol.strip().upper().zfill(6)
    item = db.scalar(
        select(WatchlistItem).where(WatchlistItem.user_id == user.id, WatchlistItem.symbol == normalized)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="观察股票不存在")
    db.delete(item)
    db.commit()
    write_audit_log(db, user, "watchlist.delete", normalized, {"symbol": normalized})
    return {"ok": True, "symbol": normalized}
