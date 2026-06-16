from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import current_user, user_feature_flags
from app.models import User
from app.schemas import AuthRequest, AuthResponse, CurrentUserOut
from app.security import create_session, hash_password, verify_password
from app.services.platform_settings import is_billing_enabled


router = APIRouter(prefix="/api/auth", tags=["auth"])


def serialize_auth(user: User, token: str, billing_enabled: bool) -> AuthResponse:
    role = getattr(user, "role", "customer")
    user._billing_enabled = billing_enabled
    return AuthResponse(
        token=token,
        email=user.email,
        role=role,
        plan=getattr(user, "plan", "free") or "free",
        billing_enabled=billing_enabled,
        is_admin=role == "admin",
        feature_flags=user_feature_flags(user, billing_enabled=billing_enabled),
    )


@router.post("/register", response_model=AuthResponse)
def register(payload: AuthRequest, db: Session = Depends(get_db)) -> AuthResponse:
    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=payload.email.lower(), password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_session(db, user)
    return serialize_auth(user, token, is_billing_enabled(db))


@router.post("/login", response_model=AuthResponse)
def login(payload: AuthRequest, db: Session = Depends(get_db)) -> AuthResponse:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    token = create_session(db, user)
    return serialize_auth(user, token, is_billing_enabled(db))


@router.get("/me", response_model=CurrentUserOut)
def me(user: User = Depends(current_user)) -> CurrentUserOut:
    role = getattr(user, "role", "customer")
    return CurrentUserOut(
        email=user.email,
        role=role,
        plan=getattr(user, "plan", "free") or "free",
        billing_enabled=getattr(user, "_billing_enabled", False),
        is_admin=role == "admin",
        status=getattr(user, "status", "active"),
        feature_flags=user_feature_flags(user),
    )
