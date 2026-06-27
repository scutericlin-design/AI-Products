from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import current_user, user_feature_flags
from app.models import PlanUpgradeRequest, User
from app.schemas import PlanUpgradeRequestIn
from app.services.audit import write_audit_log
from app.services.platform_settings import is_billing_enabled
from app.services.timezone import beijing_iso


router = APIRouter(prefix="/api/billing", tags=["billing"])

PLAN_CATALOG: dict[str, dict[str, Any]] = {
    "free": {
        "code": "free",
        "name": "基础版",
        "price_monthly_cny": 0,
        "price_yearly_cny": 0,
        "pool_limit": 10,
        "features": ["Top 10 系统股票池", "个人持股建议", "观察池"],
    },
    "pro": {
        "code": "pro",
        "name": "高级版",
        "price_monthly_cny": 99,
        "price_yearly_cny": 999,
        "pool_limit": 30,
        "features": ["Top 30 系统股票池", "组合回测与风控", "数据导出申请", "专业化中心", "优先使用 TuShare 缓存"],
    },
}


def serialize_upgrade_request(item: PlanUpgradeRequest) -> dict[str, Any]:
    return {
        "id": item.id,
        "target_plan": item.target_plan,
        "billing_cycle": item.billing_cycle,
        "amount_cny": item.amount_cny,
        "status": item.status,
        "requested_at": beijing_iso(item.requested_at),
        "decided_at": beijing_iso(item.decided_at),
        "decision_note": item.decision_note,
    }


@router.get("/plans")
def plans(user: User = Depends(current_user), db: Session = Depends(get_db)):
    billing_enabled = is_billing_enabled(db)
    user._billing_enabled = billing_enabled
    current_plan = getattr(user, "plan", "free") or "free"
    return {
        "current_plan": current_plan,
        "effective_plan": current_plan if billing_enabled else "pro_unlimited",
        "billing_enabled": billing_enabled,
        "is_admin": getattr(user, "role", "customer") == "admin",
        "feature_flags": user_feature_flags(user, billing_enabled=billing_enabled),
        "plans": list(PLAN_CATALOG.values()),
    }


@router.get("/me")
def billing_me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    billing_enabled = is_billing_enabled(db)
    user._billing_enabled = billing_enabled
    rows = db.scalars(
        select(PlanUpgradeRequest)
        .where(PlanUpgradeRequest.user_id == user.id)
        .order_by(PlanUpgradeRequest.requested_at.desc())
        .limit(20)
    ).all()
    return {
        "plan": getattr(user, "plan", "free") or "free",
        "effective_plan": getattr(user, "plan", "free") if billing_enabled else "pro_unlimited",
        "billing_enabled": billing_enabled,
        "feature_flags": user_feature_flags(user, billing_enabled=billing_enabled),
        "requests": [serialize_upgrade_request(item) for item in rows],
    }


@router.post("/upgrade-requests")
def create_upgrade_request(
    payload: PlanUpgradeRequestIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not is_billing_enabled(db):
        raise HTTPException(status_code=400, detail="收费功能尚未启用，当前所有用户均为高级无限制版")
    current_plan = getattr(user, "plan", "free") or "free"
    if current_plan == "pro":
        raise HTTPException(status_code=400, detail="当前账户已经是高级版")
    pending = db.scalar(
        select(PlanUpgradeRequest).where(
            PlanUpgradeRequest.user_id == user.id,
            PlanUpgradeRequest.status == "pending",
        )
    )
    if pending is not None:
        return serialize_upgrade_request(pending)
    catalog = PLAN_CATALOG[payload.target_plan]
    amount = catalog["price_yearly_cny"] if payload.billing_cycle == "yearly" else catalog["price_monthly_cny"]
    item = PlanUpgradeRequest(
        user_id=user.id,
        target_plan=payload.target_plan,
        billing_cycle=payload.billing_cycle,
        amount_cny=amount,
        status="pending",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    write_audit_log(
        db,
        user,
        "billing.upgrade_request",
        payload.target_plan,
        {"request_id": item.id, "billing_cycle": item.billing_cycle, "amount_cny": item.amount_cny},
    )
    return serialize_upgrade_request(item)
