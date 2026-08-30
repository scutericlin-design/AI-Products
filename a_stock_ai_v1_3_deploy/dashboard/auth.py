from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings


COOKIE_NAME = "a_stock_dashboard_session"
HASH_ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 260_000
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class AuthIdentity:
    email: str
    role: str
    source: str


def auth_enabled() -> bool:
    return _bool_env("DASHBOARD_AUTH_ENABLED", True)


def configured_username() -> str:
    return os.getenv("DASHBOARD_AUTH_USERNAME", "admin").strip() or "admin"


def session_seconds() -> int:
    try:
        return max(int(os.getenv("DASHBOARD_SESSION_SECONDS", "28800")), 300)
    except ValueError:
        return 28800


def password_hash_configured() -> bool:
    return bool(os.getenv("DASHBOARD_AUTH_PASSWORD_HASH", "").strip())


def verify_credentials(username: str, password: str) -> AuthIdentity | None:
    init_auth_db()
    normalized = normalize_email(username)
    expected_user = configured_username()
    stored_hash = os.getenv("DASHBOARD_AUTH_PASSWORD_HASH", "").strip()
    if stored_hash and hmac.compare_digest(username.strip(), expected_user) and verify_password(password, stored_hash):
        return AuthIdentity(email=expected_user, role="admin", source="env_admin")
    if not normalized:
        return None
    with _connect_auth_db() as db:
        row = db.execute(
            """
            SELECT email, password_hash, role, status
            FROM dashboard_users
            WHERE email = ?
            """,
            (normalized,),
        ).fetchone()
        if not row or str(row["status"]) != "active":
            return None
        if not verify_password(password, str(row["password_hash"] or "")):
            return None
        db.execute(
            "UPDATE dashboard_users SET last_login_at = ?, updated_at = ? WHERE email = ?",
            (_now(), _now(), normalized),
        )
        return AuthIdentity(email=normalized, role=str(row["role"] or "viewer"), source="dashboard_user")


def make_session_cookie(identity: AuthIdentity) -> str:
    expires_at = int(time.time()) + session_seconds()
    payload = {
        "u": identity.email,
        "r": identity.role,
        "s": identity.source,
        "exp": expires_at,
        "n": secrets.token_urlsafe(12),
    }
    encoded = _b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signature = _sign(encoded)
    return f"{encoded}.{signature}"


def verify_session_cookie(cookie_header: str | None) -> bool:
    return current_identity(cookie_header) is not None


def current_identity(cookie_header: str | None) -> AuthIdentity | None:
    if not auth_enabled():
        return AuthIdentity(email="anonymous", role="admin", source="auth_disabled")
    if not cookie_header:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return None
    morsel = cookie.get(COOKIE_NAME)
    if morsel is None:
        return None
    raw_value = morsel.value
    try:
        encoded, signature = raw_value.rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(encoded), signature):
        return None
    try:
        payload = json.loads(_unb64(encoded).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    try:
        if int(payload.get("exp") or 0) < int(time.time()):
            return None
    except (TypeError, ValueError):
        return None
    email = str(payload.get("u") or "")
    role = str(payload.get("r") or "viewer")
    source = str(payload.get("s") or "session")
    if role == "admin" and email == configured_username():
        return AuthIdentity(email=email, role="admin", source=source)
    normalized = normalize_email(email)
    if not normalized:
        return None
    init_auth_db()
    with _connect_auth_db() as db:
        row = db.execute(
            "SELECT email, role, status FROM dashboard_users WHERE email = ?",
            (normalized,),
        ).fetchone()
    if not row or str(row["status"]) != "active":
        return None
    return AuthIdentity(email=normalized, role=str(row["role"] or "viewer"), source=source)


def is_admin(identity: AuthIdentity | None) -> bool:
    return bool(identity and identity.role == "admin")


def normalize_email(value: str) -> str:
    return value.strip().lower()


def create_access_request(email: str, name: str, password: str, note: str = "") -> dict[str, Any]:
    init_auth_db()
    normalized = normalize_email(email)
    if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
        return {"ok": False, "error": "邮箱格式无效"}
    if len(password) < 8:
        return {"ok": False, "error": "密码至少 8 位"}
    password_hash = make_password_hash(password)
    now = _now()
    with _connect_auth_db() as db:
        user = db.execute("SELECT status FROM dashboard_users WHERE email = ?", (normalized,)).fetchone()
        if user and str(user["status"]) == "active":
            return {"ok": False, "error": "该邮箱已开通，请直接登录"}
        existing = db.execute(
            "SELECT id, status FROM dashboard_access_requests WHERE email = ?",
            (normalized,),
        ).fetchone()
        if existing and str(existing["status"]) == "pending":
            db.execute(
                """
                UPDATE dashboard_access_requests
                SET name = ?, password_hash = ?, note = ?, updated_at = ?
                WHERE email = ?
                """,
                (name.strip(), password_hash, note.strip(), now, normalized),
            )
            return {"ok": True, "status": "pending_updated", "email": normalized}
        db.execute(
            """
            INSERT INTO dashboard_access_requests (
                email, name, password_hash, note, status, requested_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (normalized, name.strip(), password_hash, note.strip(), "pending", now, now),
        )
    return {"ok": True, "status": "pending", "email": normalized}


def list_access_requests() -> list[dict[str, Any]]:
    init_auth_db()
    with _connect_auth_db() as db:
        rows = db.execute(
            """
            SELECT id, email, name, note, status, requested_at, reviewed_at, reviewed_by, review_note, updated_at
            FROM dashboard_access_requests
            ORDER BY
              CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
              requested_at DESC
            LIMIT 200
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_dashboard_users() -> list[dict[str, Any]]:
    init_auth_db()
    with _connect_auth_db() as db:
        rows = db.execute(
            """
            SELECT email, name, role, status, approved_at, approved_by, last_login_at, created_at, updated_at
            FROM dashboard_users
            ORDER BY created_at DESC
            LIMIT 200
            """
        ).fetchall()
    return [dict(row) for row in rows]


def approve_access_request(request_id: int, admin_email: str) -> dict[str, Any]:
    return _review_access_request(request_id, admin_email, "approved")


def reject_access_request(request_id: int, admin_email: str) -> dict[str, Any]:
    return _review_access_request(request_id, admin_email, "rejected")


def set_user_status(email: str, status: str, admin_email: str) -> dict[str, Any]:
    normalized = normalize_email(email)
    if status not in {"active", "disabled"}:
        return {"ok": False, "error": "invalid_status"}
    init_auth_db()
    with _connect_auth_db() as db:
        row = db.execute("SELECT email FROM dashboard_users WHERE email = ?", (normalized,)).fetchone()
        if not row:
            return {"ok": False, "error": "user_not_found"}
        db.execute(
            """
            UPDATE dashboard_users
            SET status = ?, updated_at = ?, approved_by = COALESCE(approved_by, ?)
            WHERE email = ?
            """,
            (status, _now(), admin_email, normalized),
        )
    return {"ok": True, "email": normalized, "status": status}


def init_auth_db(path: Path | None = None) -> None:
    target = path or settings.db_path
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as db:
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS dashboard_access_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT,
                password_hash TEXT NOT NULL,
                note TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by TEXT,
                review_note TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dashboard_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                status TEXT NOT NULL DEFAULT 'active',
                approved_at TEXT,
                approved_by TEXT,
                last_login_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


def _review_access_request(request_id: int, admin_email: str, status: str) -> dict[str, Any]:
    init_auth_db()
    now = _now()
    with _connect_auth_db() as db:
        row = db.execute(
            "SELECT * FROM dashboard_access_requests WHERE id = ?",
            (int(request_id),),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "request_not_found"}
        if status == "approved":
            db.execute(
                """
                INSERT INTO dashboard_users (
                    email, name, password_hash, role, status, approved_at, approved_by, created_at, updated_at
                )
                VALUES (?, ?, ?, 'viewer', 'active', ?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    name = excluded.name,
                    password_hash = excluded.password_hash,
                    role = 'viewer',
                    status = 'active',
                    approved_at = excluded.approved_at,
                    approved_by = excluded.approved_by,
                    updated_at = excluded.updated_at
                """,
                (
                    row["email"],
                    row["name"],
                    row["password_hash"],
                    now,
                    admin_email,
                    now,
                    now,
                ),
            )
        db.execute(
            """
            UPDATE dashboard_access_requests
            SET status = ?, reviewed_at = ?, reviewed_by = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, now, admin_email, now, int(request_id)),
        )
    return {"ok": True, "request_id": int(request_id), "status": status}


def _connect_auth_db() -> sqlite3.Connection:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.db_path)
    connection.row_factory = sqlite3.Row
    return connection


def make_password_hash(password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{HASH_ALGORITHM}:{iterations}:{_b64(salt)}:{_b64(digest)}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        separator = "$" if "$" in stored_hash else ":"
        algorithm, iterations_raw, salt_raw, digest_raw = stored_hash.split(separator, 3)
        iterations = int(iterations_raw)
    except ValueError:
        return False
    if algorithm != HASH_ALGORITHM or iterations <= 0:
        return False
    try:
        salt = _unb64(salt_raw)
        expected_digest = _unb64(digest_raw)
    except ValueError:
        return False
    actual_digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual_digest, expected_digest)


def clear_cookie_header(path: str) -> str:
    return f"{COOKIE_NAME}=; Path={path}; Max-Age=0; HttpOnly; SameSite=Lax"


def session_cookie_header(value: str, path: str) -> str:
    return f"{COOKIE_NAME}={value}; Path={path}; Max-Age={session_seconds()}; HttpOnly; SameSite=Lax"


def _sign(value: str) -> str:
    secret = _auth_secret()
    digest = hmac.new(secret, value.encode("utf-8"), hashlib.sha256).digest()
    return _b64(digest)


def _auth_secret() -> bytes:
    configured = os.getenv("DASHBOARD_AUTH_SECRET", "").strip()
    if configured:
        return configured.encode("utf-8")
    fallback = os.getenv("DASHBOARD_AUTH_PASSWORD_HASH", "").strip() or "dashboard-dev-secret"
    return fallback.encode("utf-8")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _now() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
