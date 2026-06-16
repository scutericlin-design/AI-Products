from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import PlatformSetting


DEFAULT_SETTINGS = {
    "billing_enabled": "false",
}


def get_setting(db: Session, key: str) -> str:
    item = db.get(PlatformSetting, key)
    if item is None:
        return DEFAULT_SETTINGS.get(key, "")
    return item.value


def set_setting(db: Session, key: str, value: str) -> PlatformSetting:
    item = db.get(PlatformSetting, key)
    if item is None:
        item = PlatformSetting(key=key, value=value)
        db.add(item)
    else:
        item.value = value
    db.commit()
    db.refresh(item)
    return item


def is_billing_enabled(db: Session) -> bool:
    return get_setting(db, "billing_enabled").lower() == "true"
