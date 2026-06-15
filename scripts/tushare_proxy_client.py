from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from sqlalchemy import select

from app.database import SessionLocal
from app.models import DataSourceConfig, User
from app.security import decrypt_secret


@dataclass(frozen=True)
class TushareCredentials:
    token: str
    base_url: str
    token_mask: str | None = None


class TushareProxyClient:
    def __init__(self, credentials: TushareCredentials, timeout: int = 30, sleep: float = 0.05):
        self.credentials = credentials
        self.timeout = timeout
        self.sleep = sleep

    def query(self, api_name: str, params: dict[str, Any] | None = None, fields: str = "", retries: int = 2) -> pd.DataFrame:
        payload = {
            "api_name": api_name,
            "token": self.credentials.token,
            "params": params or {},
            "fields": fields,
        }
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = requests.post(
                    self.credentials.base_url.rstrip("/"),
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json()
                if data.get("code") not in {0, "0"}:
                    raise RuntimeError(data.get("msg") or f"TuShare proxy error code={data.get('code')}")
                payload_data = data.get("data") or {}
                fields_out = payload_data.get("fields") or []
                items = payload_data.get("items") or []
                frame = pd.DataFrame(items, columns=fields_out)
                time.sleep(self.sleep)
                return frame
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(0.8 * (attempt + 1))
        raise RuntimeError(f"{api_name} failed: {last_error}") from last_error


def credentials_from_db(email: str | None = None) -> TushareCredentials:
    db = SessionLocal()
    try:
        user_query = select(User)
        if email:
            user_query = user_query.where(User.email == email.lower())
        user = db.scalar(user_query.order_by(User.id.asc()))
        if user is None:
            raise RuntimeError("No user found for TuShare credentials.")
        config = db.scalar(
            select(DataSourceConfig).where(
                DataSourceConfig.user_id == user.id,
                DataSourceConfig.provider == "tushare",
            )
        )
        if config is None or not config.api_token_cipher:
            raise RuntimeError("TuShare Pro data source is not configured.")
        token = decrypt_secret(config.api_token_cipher)
        if not token:
            raise RuntimeError("TuShare Pro token is empty.")
        return TushareCredentials(
            token=token,
            base_url=config.base_url or "https://teajoin.com",
            token_mask=config.token_mask,
        )
    finally:
        db.close()


def read_cached_csv(path: Path, dtype: dict[str, str] | None = None) -> pd.DataFrame | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    return pd.read_csv(path, dtype=dtype or {}, encoding="utf-8-sig")
