from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STEPFUN_DECISION_MODEL = "stepfun-ai/step-3.7-flash"
MINIMAX_DECISION_FALLBACK_MODEL = "minimaxai/minimax-m3"


def normalize_stepfun_primary_model(value: str | None) -> str:
    model = (value or STEPFUN_DECISION_MODEL).strip()
    if not model or model.startswith("minimaxai/") or model == "minimax-production-model":
        return STEPFUN_DECISION_MODEL
    return model


def normalize_minimax_fallback_model(value: str | None) -> str | None:
    model = (value or MINIMAX_DECISION_FALLBACK_MODEL).strip()
    if not model:
        return None
    if model == "minimax-production-model":
        return MINIMAX_DECISION_FALLBACK_MODEL
    if model.startswith("stepfun-ai/"):
        return MINIMAX_DECISION_FALLBACK_MODEL
    return model


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
    trading_minimax_model: str = STEPFUN_DECISION_MODEL
    trading_minimax_fallback_model: str | None = MINIMAX_DECISION_FALLBACK_MODEL
    trading_feishu_webhook_url: str | None = None
    trading_health_max_stale_seconds: int = 300

    knowledge_enabled: bool = True
    knowledge_command_secret: str | None = None
    knowledge_public_base_url: str | None = None
    knowledge_download_token_ttl_seconds: int = 60 * 60 * 24 * 7
    knowledge_study_workspace: str = "notebooklm"
    knowledge_study_workspace_label: str = "NotebookLM"
    knowledge_stack_dir: Path = PROJECT_ROOT / "knowledge-base-stack"
    knowledge_research_inputs_dir: Path = PROJECT_ROOT / "knowledge-base-stack" / "research-pack-inputs"
    knowledge_source_packs_dir: Path = PROJECT_ROOT / "knowledge-base-stack" / "notebooklm-source-packs"
    knowledge_github_archive_enabled: bool = False
    knowledge_github_archive_repo_url: str | None = None
    knowledge_github_archive_dir: Path = PROJECT_ROOT / "data" / "knowledge-github-archive"
    knowledge_github_archive_branch: str = "main"
    knowledge_github_archive_path_prefix: str = ""
    knowledge_github_archive_author_name: str = "Hermes Knowledge Bot"
    knowledge_github_archive_author_email: str = "hermes-knowledge-bot@users.noreply.github.com"
    knowledge_github_archive_ssh_key_file: Path | None = None
    knowledge_github_archive_include_text: bool = False
    knowledge_github_archive_push: bool = True
    notion_api_key: str | None = None
    notion_version: str = "2022-06-28"
    notion_setup_json: Path = PROJECT_ROOT / "knowledge-base-stack" / "notion-setup-result.json"

    llm_provider: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str = "Qwen/Qwen2.5-7B-Instruct"
    siliconflow_api_key: str | None = None
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_model: str = "Qwen/Qwen2.5-7B-Instruct"

    feishu_verification_token: str | None = None
    feishu_bot_app_id: str | None = None
    feishu_bot_app_secret: str | None = None
    feishu_reply_enabled: bool = True
    knowledge_feishu_webhook_url: str | None = None

    google_drive_enabled: bool = False
    google_drive_folder_id: str | None = None
    google_drive_share_with_email: str | None = None
    google_service_account_file: Path | None = None
    google_service_account_json: str | None = None
    google_oauth_token_file: Path | None = None
    google_oauth_token_json: str | None = None

    onyx_workspace_url: str | None = None
    onyx_sync_webhook_url: str | None = None
    onyx_api_key: str | None = None
    ima_workspace_url: str = "https://ima.qq.com/"
    ima_default_space_url: str | None = None
    notebooklm_workspace_url: str = "https://notebooklm.google.com/"
    notebooklm_default_notebook_url: str | None = None

    @model_validator(mode="after")
    def normalize_ai_decision_models(self):
        self.trading_minimax_model = normalize_stepfun_primary_model(self.trading_minimax_model)
        self.trading_minimax_fallback_model = normalize_minimax_fallback_model(
            self.trading_minimax_fallback_model
        )
        if self.trading_minimax_fallback_model == self.trading_minimax_model:
            self.trading_minimax_fallback_model = MINIMAX_DECISION_FALLBACK_MODEL
        return self


settings = Settings()
