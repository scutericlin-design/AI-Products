from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_feature
from app.models import User, WatchlistItem
from app.routers.system_pool import MODEL_VERSION, get_or_create_config, serialize_config
from app.schemas import WatchlistIn, WatchlistOut
from app.services.audit import write_audit_log
from app.services.portfolio_advice import build_watchlist_scores
from app.services.score_refresh import ready_tushare_owner, run_tushare_pool_refresh
from app.services.timezone import now_beijing_iso


router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


def _user_watchlist(user: User, db: Session) -> list[WatchlistItem]:
    return db.scalars(
        select(WatchlistItem).where(WatchlistItem.user_id == user.id).order_by(WatchlistItem.created_at.desc())
    ).all()


@router.get("", response_model=list[WatchlistOut])
def list_watchlist(user: User = Depends(require_feature("portfolio")), db: Session = Depends(get_db)):
    return _user_watchlist(user, db)


@router.get("/scores")
def list_watchlist_scores(user: User = Depends(require_feature("portfolio")), db: Session = Depends(get_db)):
    return build_watchlist_scores(_user_watchlist(user, db))


@router.post("/refresh")
def refresh_watchlist(user: User = Depends(require_feature("portfolio")), db: Session = Depends(get_db)):
    items = _user_watchlist(user, db)
    if not items:
        return {
            "ok": True,
            "refreshed": False,
            "message": "暂无观察股票，不需要刷新评分。",
            "rows": [],
            "model_version": MODEL_VERSION,
            "refreshed_at": now_beijing_iso(),
        }

    config = get_or_create_config(user, db)
    credential_owner = ready_tushare_owner(user, db)
    refreshed = False
    engine = "cached_scores"
    message = "已使用最新缓存评分生成个人观察池。"
    stdout = ""
    if credential_owner is not None:
        refresh_result = run_tushare_pool_refresh(config, credential_owner)
        stdout = refresh_result.stdout
        if refresh_result.ok:
            refreshed = True
            engine = "tushare_pro"
            message = "已复用刚刚完成的 TuShare Pro 刷新结果，更新个人观察池评分。" if refresh_result.reused_recent_result else "已调用 TuShare Pro 更新最新行情、评分和个人观察池。"
            write_audit_log(
                db,
                user,
                "watchlist.score_refresh",
                MODEL_VERSION,
                {
                    "credential_owner": credential_owner.email,
                    "watchlist_count": len(items),
                    "reused_recent_result": refresh_result.reused_recent_result,
                    "config": serialize_config(config),
                },
            )
        else:
            message = "TuShare 刷新失败，已回退到现有评分文件。"
            write_audit_log(
                db,
                user,
                "watchlist.score_refresh_failed",
                MODEL_VERSION,
                {
                    "credential_owner": credential_owner.email,
                    "stdout": refresh_result.stdout[-1200:],
                    "stderr": refresh_result.stderr[-1200:],
                },
            )
    else:
        message = "未找到可用 TuShare Pro 数据源，已使用现有评分文件生成观察池。"

    return {
        "ok": True,
        "refreshed": refreshed,
        "engine": engine,
        "model_version": MODEL_VERSION,
        "message": message,
        "rows": build_watchlist_scores(items),
        "refreshed_at": now_beijing_iso(),
        "stdout": stdout,
    }


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
