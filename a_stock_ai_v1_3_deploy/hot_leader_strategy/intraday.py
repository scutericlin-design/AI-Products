from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from hot_leader_strategy.config import HotLeaderSettings, load_settings
from hot_leader_strategy.data_client import HotLeaderDataClient
from hot_leader_strategy.notify import send_execution_report
from hot_leader_strategy.paper import ACCOUNT_ID, HotLeaderPaperRunner
from hot_leader_strategy.storage import HotLeaderStore
from scheduler.trading_calendar import current_trading_window


TZ = ZoneInfo("Asia/Shanghai")


class HotLeaderIntradayMonitor:
    """Monitor a prior-close hot-leader plan without changing its daily alpha model.

    The monitor intentionally scans only the plan candidates and this strategy's
    current paper positions. It supplies intraday price confirmation and exit
    handling, but does not run a second all-market theme ranking every minutes.
    """

    def __init__(
        self,
        settings: HotLeaderSettings | None = None,
        store: HotLeaderStore | None = None,
        client: HotLeaderDataClient | None = None,
    ):
        self.settings = settings or load_settings()
        self.store = store or HotLeaderStore(self.settings.db_path)
        self.client = client or HotLeaderDataClient(self.settings)

    def run_once(self, now: datetime | None = None) -> dict[str, Any]:
        checked_at = _beijing(now)
        if not self.settings.auto_enabled or not self.settings.intraday_enabled:
            return {"status": "disabled", "no_real_orders": True, "checked_at": _timestamp(checked_at)}

        window = current_trading_window(checked_at)
        if not window.is_open:
            return {
                "status": "skipped_outside_trading_window",
                "no_real_orders": True,
                "checked_at": window.checked_at,
                "reason": window.reason,
            }

        trade_date = checked_at.strftime("%Y%m%d")
        account = self.store.paper_snapshot(ACCOUNT_ID, self.settings.paper_initial_cash)
        positions = {str(row["symbol"]): row for row in account.get("positions") or []}
        latest = self.store.latest_signal()
        plan = latest.get("payload") or {}
        candidates = plan.get("recommendations") or []
        actionable_plan = bool(plan and _compact_date(plan.get("as_of")) < trade_date)

        symbols = sorted(
            {
                str(row.get("symbol") or "")
                for row in [*candidates, *positions.values()]
                if str(row.get("symbol") or "")
            }
        )
        if not symbols:
            return {
                "status": "no_intraday_watchlist",
                "no_real_orders": True,
                "checked_at": _timestamp(checked_at),
                "plan_as_of": plan.get("as_of"),
            }

        try:
            quotes = self.client.realtime_prices(symbols)
        except Exception as exc:
            self.store.log_quality("intraday_quote", "blocked", f"盘中报价不可用：{exc}", trade_date)
            return {
                "status": "blocked",
                "no_real_orders": True,
                "checked_at": _timestamp(checked_at),
                "reason": f"盘中报价不可用：{exc}",
                "watchlist_count": len(symbols),
            }

        references = _reference_prices(candidates, positions)
        snapshots = [
            {
                "symbol": symbol,
                "price": price,
                "reference_price": references.get(symbol),
                "change_pct": _change_pct(price, references.get(symbol)),
                "source": "tushare:realtime_quote",
            }
            for symbol, price in quotes.items()
            if price > 0
        ]
        snapshot_count = self.store.save_intraday_snapshots(trade_date, snapshots)

        buy_candidates: list[dict[str, Any]] = []
        blocked_entries: list[dict[str, str]] = []
        if actionable_plan and self.settings.intraday_entry_enabled and _before_buy_cutoff(checked_at, self.settings.intraday_buy_cutoff):
            for candidate in candidates:
                symbol = str(candidate.get("symbol") or "")
                if not symbol or symbol in positions:
                    continue
                price = float(quotes.get(symbol) or 0)
                allowed, reason = self._entry_allowed(candidate, price, checked_at)
                if allowed:
                    buy_candidates.append(
                        {
                            **candidate,
                            "current_price": price,
                            "execution_price": price,
                            "reasoning": f"{candidate.get('reasoning') or ''}；盘中价格确认：{price:.2f}。",
                        }
                    )
                elif reason:
                    blocked_entries.append({"symbol": symbol, "reason": reason})

        execution_plan = self._execution_plan(plan, positions, buy_candidates, quotes)
        result: dict[str, Any]
        if self.settings.paper_enabled and execution_plan["recommendations"]:
            result = HotLeaderPaperRunner(self.settings, self.store).run(
                execution_plan,
                trade_date=checked_at.strftime("%Y-%m-%d"),
                execution_prices=quotes,
            )
        else:
            result = {
                "status": "monitor_only" if execution_plan["recommendations"] else "no_tradeable_quote",
                "mode": "hot_leader_local_paper_only",
                "no_real_orders": True,
                "orders": [],
                "account": self.store.paper_snapshot(ACCOUNT_ID, self.settings.paper_initial_cash),
                "plan": execution_plan,
            }

        filled = [row for row in result.get("orders") or [] if row.get("status") == "filled"]
        event_rows = []
        for order in filled:
            event_type = str(order.get("side") or "").upper()
            symbol = str(order.get("symbol") or "")
            if not symbol or self.store.intraday_event_in_cooldown(symbol, event_type, self.settings.intraday_event_cooldown_minutes, checked_at):
                continue
            dedupe_key = f"{trade_date}:{event_type}:{symbol}:{order.get('dedupe_key') or ''}"
            if self.store.record_intraday_event(
                dedupe_key=dedupe_key,
                trade_date=trade_date,
                symbol=symbol,
                event_type=event_type,
                status="filled",
                payload=order,
            ):
                event_rows.append(order)

        push = send_execution_report({**result, "orders": event_rows}, self.settings)
        result.update(
            {
                "checked_at": _timestamp(checked_at),
                "plan_as_of": plan.get("as_of"),
                "watchlist_count": len(symbols),
                "quote_count": len(quotes),
                "snapshot_count": snapshot_count,
                "entry_candidates": [row.get("symbol") for row in buy_candidates],
                "entry_blocked": blocked_entries[:20],
                "events": event_rows,
                "push": push,
                "no_real_orders": True,
            }
        )
        return result

    def _entry_allowed(self, candidate: dict[str, Any], price: float, now: datetime) -> tuple[bool, str]:
        symbol = str(candidate.get("symbol") or "")
        reference = float(candidate.get("trigger_price") or candidate.get("current_price") or 0)
        if price <= 0 or reference <= 0:
            return False, "盘中价格缺失"
        if self.store.intraday_event_in_cooldown(symbol, "BUY", self.settings.intraday_event_cooldown_minutes, now):
            return False, "买入事件仍在冷却"
        change = price / reference - 1
        if change < self.settings.intraday_entry_min_change:
            return False, "弱于计划允许的回撤范围"
        if change > self.settings.intraday_entry_max_change:
            return False, "盘中涨幅过大，禁止追高"
        if change >= _limit_pct(symbol) - self.settings.intraday_near_limit_buffer:
            return False, "接近涨停，禁止追入"
        return True, ""

    def _execution_plan(
        self,
        plan: dict[str, Any],
        positions: dict[str, dict[str, Any]],
        buy_candidates: list[dict[str, Any]],
        quotes: dict[str, float],
    ) -> dict[str, Any]:
        execution = deepcopy(plan) if plan else {}
        execution.setdefault("strategy_id", "hot_theme_leader")
        execution.setdefault("strategy_version", self.settings.strategy_version)
        execution.setdefault("as_of", datetime.now(TZ).strftime("%Y%m%d"))
        execution["execution"] = "intraday_price_confirmation_paper_only"
        execution["recommendations"] = list(buy_candidates)
        known = {str(row.get("symbol") or "") for row in buy_candidates}
        for symbol, position in positions.items():
            if symbol in known:
                continue
            execution["recommendations"].append(
                {
                    "symbol": symbol,
                    "name": position.get("name") or symbol,
                    "industry": position.get("industry") or "未分类",
                    "action": "HOLD",
                    "trigger_price": float(quotes.get(symbol) or position.get("last_price") or 0),
                    "current_price": float(quotes.get(symbol) or position.get("last_price") or 0),
                    "target_weight": 0.0,
                    "stop_loss": position.get("stop_loss"),
                    "take_profit": position.get("take_profit"),
                    "trailing_stop_pct": position.get("trailing_stop_pct"),
                    "reasoning": position.get("strategy_reason") or "热点龙头持仓盘中风控监控",
                }
            )
        return execution


def _reference_prices(candidates: list[dict[str, Any]], positions: dict[str, dict[str, Any]]) -> dict[str, float]:
    result = {
        str(row.get("symbol") or ""): float(row.get("trigger_price") or row.get("current_price") or 0)
        for row in candidates
        if str(row.get("symbol") or "")
    }
    for symbol, position in positions.items():
        result.setdefault(symbol, float(position.get("avg_cost") or position.get("last_price") or 0))
    return result


def _change_pct(price: float, reference: float | None) -> float | None:
    return round((price / reference - 1) * 100, 4) if reference and reference > 0 else None


def _limit_pct(symbol: str) -> float:
    return 0.30 if symbol.endswith(".BJ") else 0.20 if symbol.startswith(("300", "688")) else 0.10


def _before_buy_cutoff(value: datetime, cutoff: str) -> bool:
    try:
        return value.time() <= time.fromisoformat(cutoff)
    except ValueError:
        return value.time() <= time(14, 45)


def _compact_date(value: object) -> str:
    return "".join(char for char in str(value or "") if char.isdigit())[:8]


def _beijing(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(TZ)
    return value.astimezone(TZ) if value.tzinfo else value.replace(tzinfo=TZ)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")
