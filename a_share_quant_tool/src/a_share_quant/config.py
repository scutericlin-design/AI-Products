from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DASHBOARD_DB_PATH = PROJECT_ROOT / "data" / "app.db"


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    reports_dir: Path = Path("reports")
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_duty_rate: float = 0.0005
    slippage_rate: float = 0.001
    lot_size: int = 100
    max_single_loss: float = 0.08
    max_daily_new_exposure: float = 1 / 3
    max_drawdown_reduce: float = 0.10
    max_drawdown_stop: float = 0.15
    max_drawdown_liquidate: float = 0.25
    qmt_live_enabled: bool = False
    qmt_userdata_path: str | None = None
    qmt_account_id: str | None = None
    tushare_token: str | None = None
    tushare_source: str = "dashboard_proxy"
    dashboard_tushare_db_path: Path = DEFAULT_DASHBOARD_DB_PATH
    dashboard_app_secret: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        def flag(name: str, default: bool = False) -> bool:
            return os.getenv(name, str(default)).lower() in {"1", "true", "yes"}

        return cls(
            data_dir=Path(os.getenv("ASQ_DATA_DIR", "data")),
            reports_dir=Path(os.getenv("ASQ_REPORTS_DIR", "reports")),
            commission_rate=float(os.getenv("ASQ_COMMISSION_RATE", "0.00025")),
            min_commission=float(os.getenv("ASQ_MIN_COMMISSION", "5")),
            stamp_duty_rate=float(os.getenv("ASQ_STAMP_DUTY_RATE", "0.0005")),
            slippage_rate=float(os.getenv("ASQ_SLIPPAGE_RATE", "0.001")),
            qmt_live_enabled=flag("ASQ_QMT_LIVE_ENABLED"),
            qmt_userdata_path=os.getenv("ASQ_QMT_USERDATA_PATH"),
            qmt_account_id=os.getenv("ASQ_QMT_ACCOUNT_ID"),
            tushare_token=os.getenv("TUSHARE_TOKEN"),
            tushare_source=os.getenv("ASQ_TUSHARE_SOURCE", "dashboard_proxy").strip().lower(),
            dashboard_tushare_db_path=Path(os.getenv("ASQ_DASHBOARD_DB_PATH", str(DEFAULT_DASHBOARD_DB_PATH))),
            dashboard_app_secret=os.getenv("ASQ_DASHBOARD_APP_SECRET") or os.getenv("APP_SECRET"),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY"),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal local dotenv loader; existing process values always win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip("\"'")
