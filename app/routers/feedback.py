from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import current_user
from app.models import FeedbackItem, User
from app.schemas import FeedbackCreateIn, FeedbackOut
from app.services.audit import write_audit_log
from app.services.timezone import as_beijing, beijing_iso


router = APIRouter(prefix="/api/feedback", tags=["feedback"])


def feedback_ticket_code(item: FeedbackItem) -> str:
    created = as_beijing(item.created_at) or datetime.utcnow()
    return item.ticket_code or f"FB{created:%Y%m%d}{item.id:04d}"


def serialize_feedback(item: FeedbackItem, db: Session) -> dict:
    user = db.get(User, item.user_id)
    handler = db.get(User, item.handled_by_user_id) if item.handled_by_user_id else None
    return {
        "id": item.id,
        "ticket_code": feedback_ticket_code(item),
        "email": user.email if user else "",
        "category": item.category,
        "title": item.title,
        "content": item.content,
        "contact": item.contact,
        "page_context": item.page_context,
        "status": item.status,
        "admin_note": item.admin_note,
        "created_at": beijing_iso(item.created_at),
        "updated_at": beijing_iso(item.updated_at),
        "resolved_at": beijing_iso(item.resolved_at),
        "handled_by": handler.email if handler else None,
    }


@router.post("", response_model=FeedbackOut)
def create_feedback(
    payload: FeedbackCreateIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    title = payload.title.strip()
    content = payload.content.strip()
    if len(title) < 2 or len(content) < 5:
        raise HTTPException(status_code=400, detail="请填写有效的标题和详细说明")
    item = FeedbackItem(
        user_id=user.id,
        category=payload.category,
        title=title,
        content=content,
        contact=(payload.contact or user.email).strip(),
        page_context=(payload.page_context or "").strip() or None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    item.ticket_code = feedback_ticket_code(item)
    db.commit()
    db.refresh(item)
    write_audit_log(
        db,
        user,
        "feedback.create",
        item.ticket_code or str(item.id),
        {"category": item.category, "page_context": item.page_context},
    )
    return serialize_feedback(item, db)


@router.get("/mine", response_model=list[FeedbackOut])
def list_my_feedback(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(FeedbackItem)
        .where(FeedbackItem.user_id == user.id)
        .order_by(FeedbackItem.created_at.desc(), FeedbackItem.id.desc())
        .limit(100)
    ).all()
    return [serialize_feedback(item, db) for item in rows]


@router.get("/{feedback_id}", response_model=FeedbackOut)
def read_my_feedback(feedback_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(FeedbackItem, feedback_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="反馈记录不存在")
    return serialize_feedback(item, db)
