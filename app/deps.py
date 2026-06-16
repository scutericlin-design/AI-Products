from __future__ import annotations

import json

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import get_user_by_token
from app.services.platform_settings import is_billing_enabled


PLAN_FEATURES: dict[str, dict[str, bool]] = {
    "free": {
        "system_pool": True,
        "portfolio": True,
        "backtest": False,
        "data_export": False,
    },
    "pro": {
        "system_pool": True,
        "portfolio": True,
        "backtest": True,
        "data_export": True,
    },
}


def current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_by_token(db, token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    if getattr(user, "status", "active") != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    user._billing_enabled = is_billing_enabled(db)
    return user


def current_admin(user: User = Depends(current_user)) -> User:
    if getattr(user, "role", "customer") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin permission required")
    return user


def user_feature_flags(user: User, billing_enabled: bool | None = None) -> dict[str, bool]:
    billing_enabled = getattr(user, "_billing_enabled", False) if billing_enabled is None else billing_enabled
    if not billing_enabled:
        return dict(PLAN_FEATURES["pro"])
    plan = getattr(user, "plan", "free") or "free"
    defaults = dict(PLAN_FEATURES.get(plan, PLAN_FEATURES["free"]))
    raw = getattr(user, "feature_flags_json", None)
    if raw:
        try:
            defaults.update({key: bool(value) for key, value in json.loads(raw).items()})
        except json.JSONDecodeError:
            pass
    if getattr(user, "role", "customer") == "admin":
        return {key: True for key in defaults}
    return defaults


def require_feature(feature_key: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if not user_feature_flags(user).get(feature_key, False):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Feature disabled: {feature_key}")
        return user

    return dependency
