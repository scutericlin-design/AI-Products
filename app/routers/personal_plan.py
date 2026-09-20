from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import current_admin
from app.models import User
from app.schemas import PersonalPlanUpdateIn
from app.services.audit import write_audit_log
from app.trading.personal_plan import PERSONAL_PLAN_VERSION, PersonalPlanStore


router = APIRouter(prefix="/api/personal-plan", tags=["personal-plan"])


@router.get("")
def get_personal_plan(admin: User = Depends(current_admin), db: Session = Depends(get_db)):
    from app.trading.buy_alerts import read_ledger
    plan = PersonalPlanStore().get(db)
    return {
        "plan": plan,
        "buy_alerts": read_ledger(db),
        "execution_mode": "manual_only",
        "note": "该计划只生成研究与人工委托建议；系统不会向任何券商提交订单。",
    }


@router.put("")
def update_personal_plan(
    payload: PersonalPlanUpdateIn,
    admin: User = Depends(current_admin),
    db: Session = Depends(get_db),
):
    try:
        plan = PersonalPlanStore().put(db, payload.plan)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    write_audit_log(
        db,
        admin,
        "personal_plan.update",
        plan.get("version", PERSONAL_PLAN_VERSION),
        {
            "candidate_count": len(plan.get("candidates") or []),
            "account_value": plan.get("account_value"),
            "available_cash": plan.get("available_cash"),
            "max_equity_amount": plan.get("max_equity_amount"),
        },
    )
    return {"ok": True, "plan": plan, "execution_mode": "manual_only"}


class BuyFeedback(BaseModel):
    outcome: str
    filled_shares: int = Field(default=0, ge=0)
    available_cash: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    execution_date: str | None = None


@router.post('/alerts/{signal_id}/resolve')
def resolve_buy(signal_id: str, payload: BuyFeedback, admin: User = Depends(current_admin),
                db: Session = Depends(get_db)):
    from datetime import date
    import json
    from app.models import PlatformSetting
    from app.services.timezone import now_beijing
    from app.trading.buy_alerts import read_ledger
    from app.trading.durable_plan import ALERT_KEY, validate
    from app.trading.personal_plan import PERSONAL_PLAN_KEY
    ledger = read_ledger(db)
    row = ledger.get(signal_id)
    if not row:
        raise HTTPException(404, '信号不存在')
    if row['state'] in ('executed', 'skipped'):
        raise HTTPException(409, '已经处理，不能重复记账')
    if payload.outcome not in ('executed', 'skipped'):
        raise HTTPException(422, 'outcome必须为executed或skipped')
    plan = PersonalPlanStore().get(db)
    if payload.outcome == 'executed':
        if not 0 < payload.filled_shares <= row['planned_shares'] or payload.available_cash is None:
            raise HTTPException(422, '必须填写实际股数（不超过提醒股数）及成交后可用现金')
        try:
            executed = date.fromisoformat(payload.execution_date or '')
        except ValueError:
            raise HTTPException(422, '必须填写成交日期YYYY-MM-DD')
        if executed > now_beijing().date() or executed < date.fromisoformat(row['created_at'][:10]):
            raise HTTPException(422, '成交日期不在信号生成至今天之间')
        symbol = row['symbol']
        existing = plan['holdings'].get(symbol, {}).get('shares', 0)
        if existing != row['held_shares']:
            raise HTTPException(409, '持仓已发生变化，请先人工对账')
        plan['holdings'][symbol] = {'shares': existing + payload.filled_shares,
                                    'last_buy_date': executed.isoformat()}
        plan['available_cash'] = payload.available_cash
        plan['account_as_of'] = now_beijing().date().isoformat()
    else:
        if payload.filled_shares:
            raise HTTPException(422, '未买入不能填写成交股数')
    row.update(state=payload.outcome, filled_shares=payload.filled_shares,
               resolved_at=now_beijing().isoformat())
    # Commit holding reconciliation and reservation release together.
    db.get(PlatformSetting, PERSONAL_PLAN_KEY).value = json.dumps(validate(plan), ensure_ascii=False)
    db.get(PlatformSetting, ALERT_KEY).value = json.dumps(ledger, ensure_ascii=False)
    db.commit()
    write_audit_log(db, admin, 'personal_plan.buy_feedback', signal_id,
                    {'outcome': payload.outcome, 'filled_shares': payload.filled_shares})
    return {'ok': True, 'signal_id': signal_id, 'outcome': payload.outcome}
