from __future__ import annotations

import base64
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from .config import Settings
from .storage import ArchiveStore


class AkshareDataClient:
    """Thin, deliberately explicit adapter around AKShare public endpoints.

    Endpoint schemas change. This client only normalizes the exact fields used by
    the strategy and archives every received response before any transformation.
    """

    def __init__(self, archive: ArchiveStore, akshare_module: object | None = None):
        self.archive = archive
        self._ak = akshare_module

    @property
    def ak(self) -> object:
        if self._ak is None:
            import akshare as ak
            self._ak = ak
        return self._ak

    def limit_up_pool(self, trade_date: str) -> pd.DataFrame:
        raw = self.ak.stock_zt_pool_em(date=trade_date)
        self.archive.write_frame("limit_up_pool", trade_date, raw, "akshare.stock_zt_pool_em")
        return raw

    def previous_limit_up_pool(self, trade_date: str) -> pd.DataFrame:
        raw = self.ak.stock_zt_pool_previous_em(date=trade_date)
        self.archive.write_frame("previous_limit_up_pool", trade_date, raw, "akshare.stock_zt_pool_previous_em")
        return raw

    def market_snapshot(self, trade_date: str) -> pd.DataFrame:
        raw = self.ak.stock_zh_a_spot_em()
        self.archive.write_frame("market_snapshot", trade_date, raw, "akshare.stock_zh_a_spot_em")
        return raw

    def broken_board_pool(self, trade_date: str) -> pd.DataFrame:
        raw = self.ak.stock_zt_pool_zbgc_em(date=trade_date)
        self.archive.write_frame("broken_board_pool", trade_date, raw, "akshare.stock_zt_pool_zbgc_em")
        return raw

    def earnings_forecast(self, report_date: str) -> pd.DataFrame:
        raw = self.ak.stock_yjyg_em(date=report_date)
        self.archive.write_frame("earnings_forecast", report_date, raw, "akshare.stock_yjyg_em")
        return raw

    def leader_board(self, trade_date: str) -> pd.DataFrame:
        raw = self.ak.stock_lhb_detail_em(start_date=trade_date, end_date=trade_date)
        self.archive.write_frame("leader_board", trade_date, raw, "akshare.stock_lhb_detail_em")
        return raw

    def margin_summary(self, trade_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        sh = self.ak.stock_margin_sse(start_date=trade_date, end_date=trade_date)
        sz = self.ak.stock_margin_szse(date=trade_date)
        self.archive.write_frame("margin_sse", trade_date, sh, "akshare.stock_margin_sse")
        self.archive.write_frame("margin_szse", trade_date, sz, "akshare.stock_margin_szse")
        return sh, sz


DEFAULT_DASHBOARD_APP_SECRET = "local-dev-change-me-before-cloud-deploy"


class TushareCallAudit:
    """Append-only local request audit.  It deliberately excludes secrets and parameters."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def write(self, *, source: str, api_name: str, ok: bool, duration_ms: float, rows: int | None = None, error: Exception | None = None) -> None:
        record: dict[str, object] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "api": api_name,
            "ok": ok,
            "duration_ms": round(duration_ms, 2),
        }
        if rows is not None:
            record["rows"] = rows
        if error is not None:
            record["error_type"] = type(error).__name__
            record["error"] = str(error)[:500]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _decrypt_dashboard_secret(value: str, app_secret: str) -> str:
    encrypted = base64.urlsafe_b64decode(value.encode("ascii"))
    seed = app_secret.encode("utf-8")
    blocks: list[bytes] = []
    counter = 0
    while sum(map(len, blocks)) < len(encrypted):
        blocks.append(hashlib.sha256(seed + str(counter).encode("utf-8")).digest())
        counter += 1
    stream = b"".join(blocks)[:len(encrypted)]
    return bytes(byte ^ stream[index] for index, byte in enumerate(encrypted)).decode("utf-8")


def _dashboard_credentials(db_path: Path, app_secret: str | None) -> tuple[str, str]:
    if not db_path.is_file():
        raise RuntimeError(f"Dashboard 数据库不存在：{db_path}")
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        row = connection.execute(
            """
            SELECT api_token_cipher, base_url
            FROM data_source_configs
            WHERE provider = 'tushare'
              AND status IN ('available', 'configured_manual_check')
              AND api_token_cipher IS NOT NULL
            ORDER BY priority ASC, id ASC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("Dashboard 未找到已验证可用的 TuShare 数据源配置")
    token = _decrypt_dashboard_secret(row[0], app_secret or DEFAULT_DASHBOARD_APP_SECRET)
    base_url = (row[1] or "https://api.tushare.pro").rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("Dashboard TuShare 数据源地址必须为 HTTPS URL")
    return token, base_url


class TushareProxyClient:
    """TuShare-compatible proxy client used by the already validated Dashboard source."""

    def __init__(self, token: str, base_url: str, audit: TushareCallAudit, session: Any | None = None):
        self._token, self._base_url, self._audit = token, base_url, audit
        self._session = session

    @property
    def session(self) -> Any:
        if self._session is None:
            import requests
            self._session = requests.Session()
        return self._session

    def query(self, api_name: str, fields: str = "", **params: object) -> pd.DataFrame:
        started = time.perf_counter()
        try:
            response = self.session.post(
                self._base_url,
                json={"api_name": api_name, "token": self._token, "params": params, "fields": fields},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") not in {0, "0"}:
                raise RuntimeError(payload.get("msg") or f"TuShare proxy error code={payload.get('code')}")
            data = payload.get("data") or {}
            frame = pd.DataFrame(data.get("items") or [], columns=data.get("fields") or [])
        except Exception as exc:
            self._audit.write(source="dashboard_proxy", api_name=api_name, ok=False, duration_ms=(time.perf_counter() - started) * 1000, error=exc)
            raise
        self._audit.write(source="dashboard_proxy", api_name=api_name, ok=True, duration_ms=(time.perf_counter() - started) * 1000, rows=len(frame))
        return frame

    def __getattr__(self, api_name: str):
        return lambda **params: self.query(api_name, **params)


class AuditedProClient:
    """Wrap the direct SDK so both configured sources have the same audit trail."""

    def __init__(self, delegate: object, audit: TushareCallAudit, source: str = "direct"):
        self._delegate, self._audit, self._source = delegate, audit, source

    def __getattr__(self, api_name: str):
        target = getattr(self._delegate, api_name)
        if not callable(target):
            return target

        def call(*args: object, **kwargs: object) -> Any:
            started = time.perf_counter()
            try:
                result = target(*args, **kwargs)
            except Exception as exc:
                self._audit.write(source=self._source, api_name=api_name, ok=False, duration_ms=(time.perf_counter() - started) * 1000, error=exc)
                raise
            rows = len(result) if isinstance(result, pd.DataFrame) else None
            self._audit.write(source=self._source, api_name=api_name, ok=True, duration_ms=(time.perf_counter() - started) * 1000, rows=rows)
            return result

        return call


class TushareDataClient:
    """Primary, archive-first TuShare Pro adapter.

    Every method requests an explicit point-in-time dataset and writes the raw
    response before strategy code sees it.  Paid endpoint permissions vary by
    TuShare account; a missing permission is a recorded pipeline failure, never
    an empty favourable dataset.
    """

    def __init__(
        self,
        archive: ArchiveStore,
        token: str | None,
        pro_client: object | None = None,
        *,
        source: str = "direct",
        dashboard_db_path: Path | None = None,
        dashboard_app_secret: str | None = None,
        audit_path: Path | None = None,
    ):
        if source not in {"direct", "dashboard_proxy"}:
            raise ValueError("ASQ_TUSHARE_SOURCE 仅支持 direct 或 dashboard_proxy")
        if source == "direct" and not token and pro_client is None:
            raise ValueError("TUSHARE_TOKEN 未配置")
        self.archive, self._token, self._pro = archive, token, pro_client
        self._source = source
        self._dashboard_db_path = dashboard_db_path
        self._dashboard_app_secret = dashboard_app_secret
        self._audit = TushareCallAudit(audit_path or Path("reports") / "tushare_call_audit.jsonl")

    @classmethod
    def from_settings(cls, archive: ArchiveStore, settings: Settings, pro_client: object | None = None) -> "TushareDataClient":
        return cls(
            archive,
            settings.tushare_token,
            pro_client=pro_client,
            source=settings.tushare_source,
            dashboard_db_path=settings.dashboard_tushare_db_path,
            dashboard_app_secret=settings.dashboard_app_secret,
            audit_path=settings.reports_dir / "tushare_call_audit.jsonl",
        )

    @property
    def pro(self) -> object:
        if self._pro is None:
            if self._source == "dashboard_proxy":
                token, base_url = _dashboard_credentials(self._dashboard_db_path or Settings().dashboard_tushare_db_path, self._dashboard_app_secret)
                self._pro = TushareProxyClient(token, base_url, self._audit)
            else:
                import tushare as ts
                self._pro = AuditedProClient(ts.pro_api(self._token), self._audit)
        return self._pro

    def _archive(self, dataset: str, trade_date: str, frame: pd.DataFrame, endpoint: str) -> pd.DataFrame:
        self.archive.write_frame(dataset, trade_date, frame, f"tushare.{endpoint}")
        return frame

    def market_snapshot(self, trade_date: str) -> pd.DataFrame:
        return self._archive("daily", trade_date, self.pro.daily(trade_date=trade_date), "daily")

    def daily_basic(self, trade_date: str) -> pd.DataFrame:
        return self._archive("daily_basic", trade_date, self.pro.daily_basic(trade_date=trade_date), "daily_basic")

    def price_limits(self, trade_date: str) -> pd.DataFrame:
        return self._archive("stk_limit", trade_date, self.pro.stk_limit(trade_date=trade_date), "stk_limit")

    def suspensions(self, trade_date: str) -> pd.DataFrame:
        return self._archive("suspend_d", trade_date, self.pro.suspend_d(suspend_type="S", trade_date=trade_date), "suspend_d")

    def stock_basic(self, trade_date: str) -> pd.DataFrame:
        return self._archive("stock_basic", trade_date, self.pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,area,industry,market,list_date"), "stock_basic")

    def limit_up_pool(self, trade_date: str) -> pd.DataFrame:
        return self._archive("limit_up_pool", trade_date, self.pro.limit_list_ths(trade_date=trade_date, limit_type="涨停池"), "limit_list_ths")

    def broken_board_pool(self, trade_date: str) -> pd.DataFrame:
        return self._archive("broken_board_pool", trade_date, self.pro.limit_list_ths(trade_date=trade_date, limit_type="炸板池"), "limit_list_ths")

    def limit_down_pool(self, trade_date: str) -> pd.DataFrame:
        return self._archive("limit_down_pool", trade_date, self.pro.limit_list_ths(trade_date=trade_date, limit_type="跌停池"), "limit_list_ths")

    def strongest_themes(self, trade_date: str) -> pd.DataFrame:
        return self._archive("limit_cpt_list", trade_date, self.pro.limit_cpt_list(trade_date=trade_date), "limit_cpt_list")

    def moneyflow(self, trade_date: str) -> pd.DataFrame:
        return self._archive("moneyflow", trade_date, self.pro.moneyflow(trade_date=trade_date), "moneyflow")

    def earnings_forecast(self, report_date: str) -> pd.DataFrame:
        return self._archive("forecast", report_date, self.pro.forecast(ann_date=report_date), "forecast")


def as_trade_date(value: str | None = None) -> str:
    return (value or date.today().strftime("%Y%m%d")).replace("-", "")
