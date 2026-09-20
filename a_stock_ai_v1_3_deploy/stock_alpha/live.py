"""Standalone paper worker. Never imports the legacy strategy, account or scheduler."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from collections import Counter
from datetime import datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import time

import requests

from stock_alpha.data import write_json
from stock_alpha.enhanced import enhanced_plan, apply_opportunities, plan_identity, VERSION
from stock_alpha.event_ai import review
from stock_alpha.governance import evaluate as evaluate_governance
from stock_alpha.live_data import LiveData
from stock_alpha.model import ModelConfig, build_targets
from stock_alpha.paper import Ledger, ACCOUNTS, TZ


HOME = Path(os.getenv("STOCK_ALPHA_HOME", Path(__file__).parent / "local_data" / "paper"))
SEED = Path(os.getenv("STOCK_ALPHA_SEED", Path(__file__).parent / "local_data" / "2018_2026"))
CONFIG = Path(os.getenv("STOCK_ALPHA_RUNTIME_CONFIG", Path(__file__).parent / "local_data" / "runtime_config.json"))


@contextmanager
def process_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "worker.lock").open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Worker:
    def __init__(self, directory: Path = HOME, seed: Path = SEED, config_path: Path = CONFIG):
        self.directory = directory
        self.config = json.loads(config_path.read_text())
        self.data = LiveData(directory / "data", seed, self.config)
        self.ledger = Ledger(directory / "paper.sqlite")

    def health(self, status: str, **extra):
        write_json(self.directory / "health.json", {"at": datetime.now(TZ).isoformat(), "status": status,
                                                     "strategy": VERSION, "paper_only": True, **extra})

    def heartbeat(self):
        write_json(self.directory / "heartbeat.json", {"at": datetime.now(TZ).isoformat()})

    def prepare(self, now: datetime | None = None):
        now = now or datetime.now(TZ)
        with process_lock(self.directory):
            return self._prepare(now)

    def _prepare(self, now: datetime):
        self.health("preparing")
        snapshot = self.data.snapshot(now)
        as_of = snapshot["as_of"]
        last_rebalance = self.ledger.state("last_rebalance")
        since = [day for day in snapshot["calendar"] if last_rebalance and last_rebalance < day <= as_of]
        due = not last_rebalance or len(since) >= 5
        today = now.strftime("%Y%m%d")
        if due:
            accounts = self.ledger.summary()["accounts"]
            current = {account: {p["symbol"]: p["qty"] * p["mark"] / accounts[account]["equity"]
                                 for p in accounts[account]["positions"]} for account in ACCOUNTS}
            baseline = build_targets(snapshot["bars"], snapshot["financials"], snapshot["memberships"],
                                     as_of, current["A_baseline"], ModelConfig())
            if baseline["status"] != "ok":
                raise RuntimeError("Baseline data or candidate gate blocked")
            metadata = snapshot["metadata"].set_index("ts_code")
            industries = metadata.industry.fillna("unknown").to_dict()
            enhanced = enhanced_plan(baseline, current["B_enhanced"], industries, snapshot["bars"], snapshot["financials"])
            candidate = enhanced_plan(baseline, current["C_ai"], industries, snapshot["bars"], snapshot["financials"])
            event_symbols = [row["ts_code"] for row in candidate["scores"]]
            events, event_status = self.data.events(event_symbols, now, snapshot["financials"], as_of)
            ai = review(candidate, events, self.directory, self.config, now)
            candidate = apply_opportunities(candidate, ai["opportunities"], events, today=today, bars=snapshot["bars"])
            plans = {"A_baseline": baseline, "B_enhanced": enhanced, "C_ai": candidate}
            for account, plan in plans.items():
                plan["ai_mode"] = ai["mode"] if account == "C_ai" else "rules_only"
                plan["plan_id"] = plan_identity(plan, account, as_of)
                plan["ready"] = True
                plan["signal_date"] = as_of
                plan["prepared_at"] = datetime.now(TZ).isoformat()
            archive = {"plans": plans, "events": events, "event_status": event_status, "ai": ai,
                       "as_of": as_of, "not_a_historical_ai_backtest": True}
            write_json(self.directory / "plans" / f"{today}.json", archive)
            self.ledger.set_state("plans", plans)
            self.ledger.set_state("last_rebalance", as_of)
            self.ledger.set_state("last_ai", {**ai, "event_status": event_status})
        self.ledger.set_state("snapshot_as_of", as_of)
        self.ledger.set_state("prepared_day", today)
        self._update_governance(now)
        self.health("prepared", as_of=as_of, rebalanced=due)
        return {"status": "prepared", "as_of": as_of, "rebalanced": due,
                "ai": self.ledger.state("last_ai", {}).get("mode")}

    def close_and_backup(self):
        now = datetime.now(TZ)
        today = now.strftime("%Y%m%d")
        with process_lock(self.directory):
            accounts = self.ledger.summary()["accounts"]
            has_positions = any(account["positions"] for account in accounts.values())
            close_pending = False
            if has_positions and self.ledger.state("close_marked_date") != today:
                # Keep the close-valuation cache separate from next-session research inputs.
                # A provider delay must not make a paper worker unhealthy after the close.
                try:
                    if today not in self.data.calendar(now):
                        raise RuntimeError("Closing-session calendar not ready")
                    frame = self.data.query("daily", cache=f"close-{today}", trade_date=today, fields="ts_code,close")
                    adjustments = self.data.query("adj_factor", cache=f"close-{today}", trade_date=today)
                    if frame.empty or adjustments.empty:
                        close_pending = True
                    else:
                        self.ledger.mark_close(today, frame.set_index("ts_code").close.to_dict(),
                                               adjustments.set_index("ts_code").adj_factor.to_dict())
                        self.ledger.set_state("close_marked_date", today)
                except RuntimeError:
                    close_pending = True
            import sqlite3
            folder = self.directory / "backups"
            folder.mkdir(exist_ok=True)
            target = folder / f"paper_{today}.sqlite"
            temporary = target.with_suffix(".tmp")
            with self.ledger.connect() as source, sqlite3.connect(temporary) as destination:
                source.backup(destination)
                if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
            destination.close()
            temporary.chmod(0o600)
            temporary.replace(target)
            for obsolete in sorted(folder.glob("paper_*.sqlite"))[:-14]:
                obsolete.unlink()
            write_json(self.directory / "summary.json", self.ledger.summary())
            self._update_governance(now)
            self.health("close_prices_pending" if close_pending else "close_backed_up", close_date=today)
        return {"status": "close_prices_pending" if close_pending else "close_backed_up", "no_orders": True}

    def cycle(self, now: datetime | None = None) -> dict:
        supplied_now = now
        now = now or datetime.now(TZ)
        with process_lock(self.directory):
            local = now.astimezone(TZ)
            window = ((9, 30) <= (local.hour, local.minute) < (11, 30)
                      or (13, 0) <= (local.hour, local.minute) < (14, 57))
            if local.weekday() >= 5 or not window:
                self.health("closed")
                return {"status": "closed", "no_orders": True}
            sessions = self.data.calendar(local)
            today = local.strftime("%Y%m%d")
            if today not in sessions:
                self.health("exchange_holiday")
                return {"status": "exchange_holiday", "no_orders": True}
            if self.ledger.state("prepared_day") != today:
                self._prepare(local)
            snapshot = self.data.snapshot(local)
            plans = self.ledger.state("plans", {})
            symbols = set()
            all_holdings = []
            for account in ACCOUNTS:
                holdings = self.ledger.holdings(account)
                all_holdings.extend(holdings.values())
                symbols.update(holdings)
                symbols.update(plans.get(account, {}).get("weights", {}))
            quotes = self.data.quotes(sorted(symbols), local)
            # Refresh the actual execution clock after network and AI work, never pretend it happened earlier.
            factors = snapshot["bars"].sort_values("trade_date").groupby("ts_code").tail(1).set_index("ts_code").adj_factor.to_dict()
            metadata = snapshot["metadata"].set_index("ts_code")
            blocked = {symbol for symbol in symbols if symbol not in metadata.index
                       or re.match(r"^\*?ST", str(metadata.loc[symbol, "name"]).upper())
                       or (local.date() - datetime.strptime(str(metadata.loc[symbol, "list_date"]), "%Y%m%d").date()).days < 250}
            earliest = min([snapshot["as_of"], *(p["mark_at"][:10].replace("-", "") for p in all_holdings)])
            corporate = self.data.corporate_actions(sorted(symbols), earliest, local)
            blocked.update(corporate)
            execution_time = datetime.now(TZ) if supplied_now is None else local
            output = {}
            for account in ACCOUNTS:
                output[account] = self.ledger.execute(account, plans.get(account, {}), quotes, execution_time,
                                                      factors=factors, blocked=blocked, corporate_actions=corporate)
            self.notify()
            write_json(self.directory / "summary.json", self.ledger.summary())
            self._update_governance(execution_time)
            filled = sum(len(value.get("orders", [])) for value in output.values())
            fresh_count = sum(quote.fresh(execution_time) for quote in quotes.values())
            reasons = dict(Counter(item["reason"] for value in output.values() for item in value.get("blocked", [])))
            self.health("data_degraded" if corporate or fresh_count < len(symbols) else "running", quote_count=len(quotes),
                        fresh_quote_count=fresh_count, corporate_action_pending=sorted(corporate), filled=filled, blocked_reasons=reasons)
            return {"status": "cycle_complete", "accounts": output, "filled": filled, "blocked_reasons": reasons,
                    "quote_count": len(quotes), "fresh_quote_count": fresh_count}

    def notify(self):
        webhook = self.config.get("feishu_webhook")
        if not webhook:
            return
        now = datetime.now(TZ)
        with self.ledger.connect() as db:
            rows = db.execute("SELECT id,payload,attempts FROM outbox WHERE delivered_at IS NULL AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY id LIMIT 12",
                              (now.isoformat(),)).fetchall()
        if not rows:
            return
        messages = ["[Stock Alpha 独立模拟盘 | 本金100万元]"]
        for row in rows:
            item = json.loads(row["payload"])
            direction = "买入" if item["side"] == "BUY" else "卖出"
            mode = "AI降级执行" if "degraded" in item["ai_mode"] else ("AI已复核" if item["ai_mode"] == "ai_reviewed" else "规则执行（无有效新增事件）")
            messages.append(f"{direction} {item['symbol']} {item['name']} | {item['price']:.2f}元 | {item['quantity']}股 | {mode} | 成交#{item['trade_id']}")
        success = False
        try:
            response = requests.post(webhook, json={"msg_type": "text", "content": {"text": "\n".join(messages)}},
                                     timeout=(5, 10), allow_redirects=False)
            body = response.json() if response.status_code == 200 else None
            success = isinstance(body, dict) and body.get("code") == 0
        except (requests.RequestException, ValueError):
            pass
        with self.ledger.connect() as db:
            for row in rows:
                delay = min(60, 5 * 2 ** min(row["attempts"], 4))
                db.execute("UPDATE outbox SET attempts=attempts+1,delivered_at=?,next_attempt_at=? WHERE id=?",
                           (now.isoformat() if success else None, (now + timedelta(minutes=delay)).isoformat(), row["id"]))

    def _update_governance(self, now: datetime) -> None:
        with self.ledger.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT account,at,nav FROM cycles ORDER BY at")]
        self.ledger.set_state("governance", evaluate_governance(rows, now))

    def guarded(self, method):
        try:
            result = method()
            print(json.dumps({"stage": "worker", **{key: value for key, value in result.items() if key != "accounts"}}), flush=True)
        except BlockingIOError:
            print(json.dumps({"stage": "worker", "status": "another_instance_holds_lock"}), flush=True)
        except Exception as exc:
            detail = str(exc)[:160] if isinstance(exc, RuntimeError) else type(exc).__name__
            self.health("blocked", error=type(exc).__name__, detail=detail)
            print(json.dumps({"stage": "worker", "status": "blocked", "error": type(exc).__name__, "detail": detail}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "prepare", "once", "status", "serve", "healthcheck"])
    args = parser.parse_args()
    if args.action == "healthcheck":
        try:
            health = json.loads((HOME / "health.json").read_text())
            heartbeat = json.loads((HOME / "heartbeat.json").read_text())
            age = (datetime.now(TZ) - datetime.fromisoformat(heartbeat["at"])).total_seconds()
            raise SystemExit(0 if age < 900 and health["status"] not in {"blocked", "data_degraded"} else 1)
        except (OSError, ValueError, KeyError):
            raise SystemExit(1)
    worker = Worker()
    if args.action == "init":
        with process_lock(HOME):
            worker.ledger.initialize()
            worker.health("initialized")
        print(json.dumps(worker.ledger.summary(), ensure_ascii=False))
    elif args.action == "status":
        print(json.dumps(worker.ledger.summary(), ensure_ascii=False))
    elif args.action == "prepare":
        print(json.dumps(worker.prepare(), ensure_ascii=False))
    elif args.action == "once":
        print(json.dumps(worker.cycle(), ensure_ascii=False))
    else:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger

        # Starting a worker never initializes or replaces an account.
        worker.ledger.summary()
        worker.health("started")
        worker.heartbeat()
        scheduler = BlockingScheduler(timezone="Asia/Shanghai", executors={"default": {"type": "threadpool", "max_workers": 1}})
        scheduler.add_job(lambda: worker.guarded(worker.cycle), CronTrigger(day_of_week="mon-fri", hour="9-14", minute="*/2", timezone="Asia/Shanghai"),
                          id="paper_cycle", max_instances=1, coalesce=True, misfire_grace_time=30)
        scheduler.add_job(lambda: worker.guarded(worker.prepare), CronTrigger(day_of_week="mon-fri", hour=8, minute=50, timezone="Asia/Shanghai"),
                          id="prepare", max_instances=1, coalesce=True, misfire_grace_time=600)
        scheduler.add_job(worker.heartbeat, "interval", minutes=2, id="heartbeat", max_instances=1, coalesce=True)
        scheduler.add_job(lambda: worker.guarded(worker.close_and_backup), CronTrigger(day_of_week="mon-fri", hour=17, minute=30, timezone="Asia/Shanghai"),
                          id="close_backup", max_instances=1, coalesce=True, misfire_grace_time=600)
        scheduler.add_job(lambda: worker.guarded(worker.close_and_backup), CronTrigger(day_of_week="mon-fri", hour=18, minute=10, timezone="Asia/Shanghai"),
                          id="close_backup_retry", max_instances=1, coalesce=True, misfire_grace_time=600)
        scheduler.start()


if __name__ == "__main__":
    main()
