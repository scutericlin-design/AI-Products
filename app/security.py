from __future__ import annotations

import hashlib
import hmac
import secrets
import base64
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import SessionToken, User


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 260_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, salt, expected = stored_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    actual = hash_password(password, salt).split("$", 2)[2]
    return hmac.compare_digest(actual, expected)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _secret_stream(length: int) -> bytes:
    seed = settings.app_secret.encode("utf-8")
    blocks: list[bytes] = []
    counter = 0
    while sum(len(block) for block in blocks) < length:
        blocks.append(hashlib.sha256(seed + str(counter).encode("utf-8")).digest())
        counter += 1
    return b"".join(blocks)[:length]


def encrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.encode("utf-8")
    stream = _secret_stream(len(raw))
    encrypted = bytes(byte ^ stream[index] for index, byte in enumerate(raw))
    return base64.urlsafe_b64encode(encrypted).decode("ascii")


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    encrypted = base64.urlsafe_b64decode(value.encode("ascii"))
    stream = _secret_stream(len(encrypted))
    raw = bytes(byte ^ stream[index] for index, byte in enumerate(encrypted))
    return raw.decode("utf-8")


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    session = SessionToken(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=datetime.utcnow() + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(session)
    db.commit()
    return token


def get_user_by_token(db: Session, token: str) -> User | None:
    session = db.scalar(
        select(SessionToken).where(
            SessionToken.token_hash == hash_token(token),
            SessionToken.expires_at > datetime.utcnow(),
        )
    )
    if session is None:
        return None
    return db.get(User, session.user_id)
