from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, User


def write_audit_log(
    db: Session,
    user: User | None,
    action: str,
    target: str | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    record = AuditLog(
        user_id=user.id if user else None,
        action=action,
        target=target,
        detail_json=json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def parse_audit_detail(record: AuditLog) -> dict[str, Any]:
    if not record.detail_json:
        return {}
    try:
        return json.loads(record.detail_json)
    except json.JSONDecodeError:
        return {"raw": record.detail_json}
