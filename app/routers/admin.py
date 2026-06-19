from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import PLAN_FEATURES, current_admin, user_feature_flags
from app.models import DataExportRequest, PlanUpgradeRequest, PortfolioPosition, StrategyConfig, UsageLog, User
from app.routers.system_pool import (
    DEFAULT_STRATEGY_TYPE,
    STRATEGY_MODEL_VERSIONS,
    create_version_snapshot,
    get_or_create_config,
    serialize_config,
)
from app.schemas import (
    AdminPlatformSettingsIn,
    AdminStrategyUpdateIn,
    AdminUserUpdateIn,
    DataExportDecisionIn,
    PlanUpgradeDecisionIn,
)
from app.services.audit import write_audit_log
from app.services.platform_settings import is_billing_enabled, set_setting


router = APIRouter(prefix="/api/admin", tags=["admin"])
CN_TZ = ZoneInfo("Asia/Shanghai")


def _china_today_utc_start() -> datetime:
    now_cn = datetime.now(CN_TZ)
    start_cn = now_cn.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_cn.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _serialize_user(user: User, db: Session, billing_enabled: bool | None = None) -> dict:
    billing_enabled = is_billing_enabled(db) if billing_enabled is None else billing_enabled
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
        "plan": getattr(user, "plan", "free") or "free",
        "billing_enabled": billing_enabled,
        "feature_flags": user_feature_flags(user, billing_enabled=billing_enabled),
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


def _serialize_upgrade_request(item: PlanUpgradeRequest, db: Session) -> dict:
    user = db.get(User, item.user_id)
    approver = db.get(User, item.decided_by_user_id) if item.decided_by_user_id else None
    return {
        "id": item.id,
        "user_id": item.user_id,
        "email": user.email if user else "",
        "target_plan": item.target_plan,
        "billing_cycle": item.billing_cycle,
        "amount_cny": item.amount_cny,
        "status": item.status,
        "requested_at": item.requested_at.isoformat() if item.requested_at else None,
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        "decided_by": approver.email if approver else None,
        "decision_note": item.decision_note,
    }


@router.get("/summary")
def summary(admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    billing_enabled = is_billing_enabled(db)
    user_count = db.scalar(select(func.count(User.id)))
    active_count = db.scalar(select(func.count(User.id)).where(User.status == "active"))
    pending_exports = db.scalar(select(func.count(DataExportRequest.id)).where(DataExportRequest.status == "pending"))
    pending_upgrades = db.scalar(select(func.count(PlanUpgradeRequest.id)).where(PlanUpgradeRequest.status == "pending"))
    admin_count = db.scalar(select(func.count(User.id)).where(User.role == "admin"))
    pro_count = db.scalar(select(func.count(User.id)).where(User.plan == "pro"))
    usage_24h = db.scalar(
        select(func.count(UsageLog.id)).where(UsageLog.created_at >= datetime.utcnow() - timedelta(days=1))
    )
    today_start = _china_today_utc_start()
    usage_today = db.scalar(select(func.count(UsageLog.id)).where(UsageLog.created_at >= today_start))
    usage_today_errors = db.scalar(
        select(func.count(UsageLog.id)).where(UsageLog.created_at >= today_start, UsageLog.status_code >= 400)
    )
    usage_today_bytes = db.scalar(
        select(func.coalesce(func.sum(UsageLog.response_bytes), 0)).where(UsageLog.created_at >= today_start)
    )
    usage_today_active_users = db.scalar(
        select(func.count(func.distinct(UsageLog.user_id))).where(
            UsageLog.created_at >= today_start,
            UsageLog.user_id.is_not(None),
        )
    )
    return {
        "user_count": int(user_count or 0),
        "active_count": int(active_count or 0),
        "admin_count": int(admin_count or 0),
        "pro_count": int(pro_count or 0),
        "pending_exports": int(pending_exports or 0),
        "pending_upgrades": int(pending_upgrades or 0),
        "usage_24h": int(usage_24h or 0),
        "usage_today": int(usage_today or 0),
        "usage_today_errors": int(usage_today_errors or 0),
        "usage_today_bytes": int(usage_today_bytes or 0),
        "usage_today_active_users": int(usage_today_active_users or 0),
        "billing_enabled": billing_enabled,
    }


@router.get("/settings")
def read_settings(admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    return {"billing_enabled": is_billing_enabled(db)}


@router.put("/settings")
def update_settings(
    payload: AdminPlatformSettingsIn,
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
):
    set_setting(db, "billing_enabled", "true" if payload.billing_enabled else "false")
    write_audit_log(
        db,
        admin,
        "admin.platform_settings_update",
        "billing_enabled",
        {"billing_enabled": payload.billing_enabled},
    )
    return {"billing_enabled": payload.billing_enabled}


@router.get("/usage")
def usage(days: int = 7, admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    days = max(1, min(days, 90))
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.scalars(select(UsageLog).where(UsageLog.created_at >= since).order_by(UsageLog.created_at.desc())).all()
    users = {user.id: user.email for user in db.scalars(select(User)).all()}
    by_day: dict[str, dict] = {}
    by_path: dict[str, dict] = {}
    by_user: dict[str, dict] = {}
    total_bytes = 0
    total_duration = 0.0
    error_count = 0
    today_start = _china_today_utc_start()
    today_active_users: set[str] = set()
    today_stats = {"requests": 0, "errors": 0, "bytes": 0, "active_users": 0}
    for row in rows:
        day = row.created_at.strftime("%Y-%m-%d") if row.created_at else "--"
        email = users.get(row.user_id, "anonymous")
        total_bytes += int(row.response_bytes or 0)
        total_duration += float(row.duration_ms or 0)
        error_count += 1 if int(row.status_code or 0) >= 400 else 0
        if row.created_at and row.created_at >= today_start:
            today_stats["requests"] += 1
            today_stats["errors"] += 1 if int(row.status_code or 0) >= 400 else 0
            today_stats["bytes"] += int(row.response_bytes or 0)
            if row.user_id is not None:
                today_active_users.add(email)
        day_bucket = by_day.setdefault(day, {"date": day, "requests": 0, "errors": 0, "bytes": 0})
        path_bucket = by_path.setdefault(row.path, {"path": row.path, "requests": 0, "errors": 0, "bytes": 0})
        user_bucket = by_user.setdefault(email, {"email": email, "requests": 0, "errors": 0, "bytes": 0})
        for bucket in (day_bucket, path_bucket, user_bucket):
            bucket["requests"] += 1
            bucket["errors"] += 1 if int(row.status_code or 0) >= 400 else 0
            bucket["bytes"] += int(row.response_bytes or 0)
    request_count = len(rows)
    today_stats["active_users"] = len(today_active_users)
    return {
        "days": days,
        "request_count": request_count,
        "error_count": error_count,
        "active_users": len([key for key in by_user if key != "anonymous"]),
        "response_bytes": total_bytes,
        "avg_duration_ms": round(total_duration / request_count, 1) if request_count else 0,
        "by_day": sorted(by_day.values(), key=lambda item: item["date"]),
        "top_paths": sorted(by_path.values(), key=lambda item: item["requests"], reverse=True)[:10],
        "top_users": sorted(by_user.values(), key=lambda item: item["requests"], reverse=True)[:10],
        "today": today_stats,
    }


@router.get("/users")
def list_users(admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    billing_enabled = is_billing_enabled(db)
    users = db.scalars(select(User).order_by(User.created_at.desc(), User.id.desc()).limit(200)).all()
    return {"rows": [_serialize_user(user, db, billing_enabled=billing_enabled) for user in users]}


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
    user.plan = payload.plan
    user.feature_flags_json = json.dumps(payload.feature_flags, ensure_ascii=False, sort_keys=True)
    db.commit()
    db.refresh(user)
    write_audit_log(
        db,
        admin,
        "admin.user_update",
        user.email,
        {
            "user_id": user.id,
            "role": user.role,
            "status": user.status,
            "plan": user.plan,
            "feature_flags": payload.feature_flags,
        },
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
    config.model_version = STRATEGY_MODEL_VERSIONS.get(
        getattr(config, "strategy_type", DEFAULT_STRATEGY_TYPE),
        STRATEGY_MODEL_VERSIONS[DEFAULT_STRATEGY_TYPE],
    )
    db.commit()
    db.refresh(config)
    create_version_snapshot(config, db, note=f"admin_config_save:{admin.email}")
    write_audit_log(db, admin, "admin.strategy_update", user.email, {"user_id": user.id, "config": serialize_config(config)})
    return serialize_config(config)


@router.get("/export-requests")
def list_export_requests(admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(DataExportRequest).order_by(DataExportRequest.requested_at.desc()).limit(200)).all()
    return {"rows": [_serialize_export_request(item, db) for item in rows]}


@router.get("/upgrade-requests")
def list_upgrade_requests(admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(PlanUpgradeRequest).order_by(PlanUpgradeRequest.requested_at.desc()).limit(200)).all()
    return {"rows": [_serialize_upgrade_request(item, db) for item in rows]}


@router.post("/upgrade-requests/{request_id}/decision")
def decide_upgrade_request(
    request_id: int,
    payload: PlanUpgradeDecisionIn,
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
):
    item = db.get(PlanUpgradeRequest, request_id)
    if item is None:
        raise HTTPException(status_code=404, detail="升级申请不存在")
    user = db.get(User, item.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    item.status = payload.status
    item.decided_at = datetime.utcnow()
    item.decided_by_user_id = admin.id
    item.decision_note = payload.note
    if payload.status == "approved":
        user.plan = item.target_plan
        user.feature_flags_json = json.dumps(PLAN_FEATURES.get(item.target_plan, PLAN_FEATURES["free"]), ensure_ascii=False)
    db.commit()
    db.refresh(item)
    write_audit_log(
        db,
        admin,
        f"admin.upgrade_{payload.status}",
        user.email,
        {"request_id": item.id, "user_id": user.id, "target_plan": item.target_plan, "note": payload.note},
    )
    return _serialize_upgrade_request(item, db)


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
