from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import settings
from data.tushare_client import TushareClient
from scheduler.trading_calendar import BEIJING_TZ


logger = logging.getLogger(__name__)


class FundamentalClient:
    """Caches slow-moving financial data so intraday cycles do not consume API quota."""

    def get_profile(self, symbol: str) -> dict[str, Any]:
        normalized = TushareClient().normalize_symbol(symbol)
        cached = self._load_cache().get(normalized)
        if cached and self._is_fresh(cached):
            return {**cached.get("profile", {}), "cache_status": "fresh"}

        profile = self._fetch_tushare(normalized)
        if profile.get("available"):
            cache = self._load_cache()
            cache[normalized] = {"cached_at": _now(), "profile": profile}
            self._save_cache(cache)
            return {**profile, "cache_status": "refreshed"}

        if cached:
            return {
                **cached.get("profile", {}),
                "cache_status": "stale_fallback",
                "warning": "财务数据刷新失败，使用过期缓存，仅允许观察",
                "available": False,
            }
        return profile

    def _fetch_tushare(self, symbol: str) -> dict[str, Any]:
        if settings.dry_run:
            return _unavailable(symbol, "dry_run 不使用虚构财务数据")
        if not settings.tushare_token:
            return _unavailable(symbol, "未配置 TuShare Token，无法验证基本面")

        try:
            import tushare as ts

            client = TushareClient()
            pro = client._proxy_pro_api(ts) if settings.tushare_base_url else ts.pro_api(settings.tushare_token)
            indicator = pro.fina_indicator(
                ts_code=symbol,
                limit=1,
                fields="ts_code,end_date,ann_date,roe,roe_waa,or_yoy,netprofit_yoy,ocfps,eps",
            )
            basic = pro.daily_basic(
                ts_code=symbol,
                limit=1,
                fields="ts_code,trade_date,pe_ttm,pb,total_mv,circ_mv",
            )
            company = pro.stock_basic(
                ts_code=symbol,
                fields="ts_code,name,industry,market,list_date",
            )
        except Exception as exc:
            logger.warning("fundamental fetch failed for %s: %s", symbol, exc)
            return _unavailable(symbol, f"TuShare 财务数据不可用：{exc}")

        indicator_row = _first_row(indicator)
        basic_row = _first_row(basic)
        company_row = _first_row(company)
        if not indicator_row:
            return _unavailable(symbol, "TuShare 未返回财务指标")

        fundamentals = {
            "roe": _number(indicator_row.get("roe") or indicator_row.get("roe_waa")),
            "revenue_yoy": _number(indicator_row.get("or_yoy")),
            "profit_yoy": _number(indicator_row.get("netprofit_yoy")),
            "operating_cashflow_per_share": _number(indicator_row.get("ocfps")),
            "eps": _number(indicator_row.get("eps")),
            "pe_ttm": _number(basic_row.get("pe_ttm")),
            "pb": _number(basic_row.get("pb")),
            "total_mv": _number(basic_row.get("total_mv")),
        }
        metric_count = sum(value is not None for value in fundamentals.values())
        if metric_count < 3:
            return _unavailable(symbol, "TuShare 财务指标字段不足，不能作为买入依据")

        return {
            "available": True,
            "symbol": symbol,
            "industry": str(company_row.get("industry") or "未分类"),
            "market": str(company_row.get("market") or ""),
            "report_end_date": str(indicator_row.get("end_date") or ""),
            "announcement_date": str(indicator_row.get("ann_date") or ""),
            "fundamentals": fundamentals,
            "source": "tushare_fina_indicator_daily_basic",
            "fetched_at": _now(),
        }

    def _load_cache(self) -> dict[str, Any]:
        path = settings.fundamental_cache_path
        if not path.exists():
            return {}
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_cache(self, cache: dict[str, Any]) -> None:
        path: Path = settings.fundamental_cache_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _is_fresh(self, entry: dict[str, Any]) -> bool:
        value = entry.get("cached_at")
        try:
            cached_at = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return False
        return cached_at >= datetime.now(BEIJING_TZ) - timedelta(hours=settings.fundamental_cache_ttl_hours)


def _first_row(frame: Any) -> dict[str, Any]:
    if frame is None or getattr(frame, "empty", True):
        return {}
    try:
        return {str(key): value for key, value in frame.iloc[0].to_dict().items()}
    except (AttributeError, IndexError):
        return {}


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if parsed == parsed else None
    except (TypeError, ValueError):
        return None


def _unavailable(symbol: str, warning: str) -> dict[str, Any]:
    return {
        "available": False,
        "symbol": symbol,
        "industry": "未验证",
        "fundamentals": {},
        "source": "unavailable",
        "warning": warning,
        "fetched_at": _now(),
    }


def _now() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
