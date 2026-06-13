from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "A-Share Alpha Lab"
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'data' / 'app.db'}"
    session_ttl_hours: int = 24 * 14
    recommended_pool_path: Path = PROJECT_ROOT / "data" / "processed" / "recommended_pool.csv"


settings = Settings()
