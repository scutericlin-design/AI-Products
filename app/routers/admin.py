from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import current_admin, user_feature_flags
from app.models import DataExportRequest, PortfolioPosition, StrategyConfig, User
from app.routers.system_pool import create_version_snapshot, get_or_create_config, serialize_config
from app.schemas import AdminStrategyUpdateIn, AdminUserUpdateIn, DataExportDecisionIn
from app.services.audit import write_audit_log


router = APIRouter(prefix="/api/admin", tags=["admin"])


def _serialize_user(user: User, db: Session) -> dict:
    position_count = db.scalar(
        select(func.count(PortfolioPosition.id)).where(PortfolioPosition.user_id == user.id)
    )
    config = db.scalar(select(StrategyConfig).where(StrategyConfig.user_id == user.id))
    return {
        "id": user.id,
        "email": user.email,
        "role": getattr(user, "role", "customer"),
        "is_admin": getattr(user, "role", "customer") == "admin",
        "status": getattr(user, "status", "active"),
        "feature_flags": user_feature_flags(user),
        "position_count": int(position_count or 0),
        "strategy": serialize_config(config) if config else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _serialize_export_request(item: DataExportRequest, db: Session) -> dict:
    user = db.get(User, item.user_id)
    approver = db.get(User, item.decided_by_user_id) if item.decided_by_user_id else None
    symbols = [symbol for symbol in (item.symbols_csv or "").split(",") if symbol]
    return {
        "id": item.id,
        "user_id": item.user_id,
        "email": user.email if user else "",
        "dataset": item.dataset,
        "symbols": symbols,
        "start_date": item.start_date,
        "end_date": item.end_date,
        "status": item.status,
        "requested_at": item.requested_at.isoformat() if item.requested_at else None,
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        "decided_by": approver.email if approver else None,
        "decision_note": item.decision_note,
    }


@router.get("/summary")
def summary(admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    user_count = db.scalar(select(func.count(User.id)))
    active_count = db.scalar(select(func.count(User.id)).where(User.status == "active"))
    pending_exports = db.scalar(select(func.count(DataExportRequest.id)).where(DataExportRequest.status == "pending"))
    admin_count = db.scalar(select(func.count(User.id)).where(User.role == "admin"))
    return {
        "user_count": int(user_count or 0),
        "active_count": int(active_count or 0),
        "admin_count": int(admin_count or 0),
        "pending_exports": int(pending_exports or 0),
    }


@router.get("/users")
def list_users(admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.created_at.desc(), User.id.desc()).limit(200)).all()
    return {"rows": [_serialize_user(user, db) for user in users]}


@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    payload: AdminUserUpdateIn,
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == admin.id and payload.role != "admin":
        raise HTTPException(status_code=400, detail="不能取消自己的 Admin 权限")
    user.status = payload.status
    user.role = payload.role
    user.feature_flags_json = json.dumps(payload.feature_flags, ensure_ascii=False, sort_keys=True)
    db.commit()
    db.refresh(user)
    write_audit_log(
        db,
        admin,
        "admin.user_update",
        user.email,
        {"user_id": user.id, "role": user.role, "status": user.status, "feature_flags": payload.feature_flags},
    )
    return _serialize_user(user, db)


@router.get("/users/{user_id}/strategy")
def read_user_strategy(user_id: int, admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return serialize_config(get_or_create_config(user, db))


@router.put("/users/{user_id}/strategy")
def update_user_strategy(
    user_id: int,
    payload: AdminStrategyUpdateIn,
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    config = get_or_create_config(user, db)
    markets = [market for market in payload.markets if market in {"main", "chinext", "star", "beijing"}]
    if not markets:
        raise HTTPException(status_code=400, detail="至少选择一个市场板块")
    for field, value in payload.model_dump().items():
        if field == "markets":
            config.market_scope = ",".join(markets)
            config.include_beijing = 1 if "beijing" in markets else 0
        elif field == "include_beijing":
            config.include_beijing = 1 if value else 0
        else:
            setattr(config, field, value)
    db.commit()
    db.refresh(config)
    create_version_snapshot(config, db, note=f"admin_config_save:{admin.email}")
    write_audit_log(db, admin, "admin.strategy_update", user.email, {"user_id": user.id, "config": serialize_config(config)})
    return serialize_config(config)


@router.get("/export-requests")
def list_export_requests(admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(DataExportRequest).order_by(DataExportRequest.requested_at.desc()).limit(200)).all()
    return {"rows": [_serialize_export_request(item, db) for item in rows]}


@router.post("/export-requests/{request_id}/decision")
def decide_export_request(
    request_id: int,
    payload: DataExportDecisionIn,
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
):
    item = db.get(DataExportRequest, request_id)
    if item is None:
        raise HTTPException(status_code=404, detail="导出申请不存在")
    item.status = payload.status
    item.decided_at = datetime.utcnow()
    item.decided_by_user_id = admin.id
    item.decision_note = payload.note
    db.commit()
    db.refresh(item)
    write_audit_log(
        db,
        admin,
        f"admin.export_{payload.status}",
        str(item.id),
        {"request_id": item.id, "user_id": item.user_id, "dataset": item.dataset, "note": payload.note},
    )
    return _serialize_export_request(item, db)
