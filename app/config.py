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
    app_secret: str = "local-dev-change-me-before-cloud-deploy"
    market_data_cache_backend: str = "database"
    market_data_cache_dir: Path = PROJECT_ROOT / "data" / "tushare"
    trading_loop_seconds: int = 60
    trading_run_on_start: bool = True
    trading_dry_run: bool = True
    trading_symbols: str = ""
    trading_max_candidates: int = 20
    trading_min_turnover_yi: float = 2.0
    trading_confidence_threshold: float = 0.62
    trading_max_position_weight: float = 0.12
    trading_push_enabled: bool = True
    trading_send_in_dry_run: bool = False
    trading_tushare_token: str | None = None
    trading_tushare_base_url: str | None = None
    trading_minimax_api_key: str | None = None
    trading_minimax_endpoint: str | None = None
    trading_minimax_model: str = "minimax-production-model"
    trading_feishu_webhook_url: str | None = None
    trading_health_max_stale_seconds: int = 300


settings = Settings()
