"""An isolated, transactional A/B/C paper ledger. No broker integration."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Shanghai")
ACCOUNTS = ("A_baseline", "B_enhanced", "C_ai")


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    price: float
    at: datetime
    source: str
    bid: float = 0.0
    ask: float = 0.0
    volume: float = 0.0
    up_limit: float = 0.0
    down_limit: float = 0.0

    def fresh(self, now: datetime) -> bool:
        return (self.at.tzinfo is not None and self.at.astimezone(TZ).date() == now.astimezone(TZ).date()
                and -5 <= (now - self.at).total_seconds() <= 120
                and math.isfinite(self.price) and self.price > 0)


class Ledger:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        if not self.path.exists():
            raise RuntimeError("Paper database missing; explicit initialization required")
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self, initial_cash: float = 1_000_000) -> None:
        if initial_cash != 1_000_000:
            raise ValueError("This experiment requires exactly 1,000,000 yuan per ledger")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        marker = self.path.with_suffix(".initialized")
        if self.path.exists() or marker.exists():
            with self.connect() as db:
                rows = db.execute("SELECT id,initial_cash FROM accounts ORDER BY id").fetchall()
                if [row["id"] for row in rows] != list(ACCOUNTS) or any(row["initial_cash"] != initial_cash for row in rows):
                    raise RuntimeError("Existing paper ledger identity mismatch; never reset automatically")
            return
        # Reserve initialization explicitly; a crash never silently creates a replacement account.
        with marker.open("x") as handle:
            handle.write("stock_alpha_paper_v1\n")
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE accounts(id TEXT PRIMARY KEY, initial_cash REAL NOT NULL, cash REAL NOT NULL);
                CREATE TABLE positions(account TEXT, symbol TEXT, name TEXT, qty INTEGER, cost REAL,
                    mark REAL, mark_at TEXT, entry_date TEXT, adj_factor REAL,
                    PRIMARY KEY(account,symbol), FOREIGN KEY(account) REFERENCES accounts(id));
                CREATE TABLE lots(account TEXT,symbol TEXT,day TEXT,qty INTEGER,
                    PRIMARY KEY(account,symbol,day));
                CREATE TABLE trades(id INTEGER PRIMARY KEY,account TEXT,cycle TEXT,plan_id TEXT,symbol TEXT,
                    name TEXT,side TEXT,qty INTEGER,price REAL,fee REAL,realized REAL,reason TEXT,at TEXT,
                    ai_mode TEXT, UNIQUE(account,cycle,symbol,side));
                CREATE TABLE attempts(account TEXT,cycle TEXT,symbol TEXT,side TEXT,status TEXT,reason TEXT,
                    UNIQUE(account,cycle,symbol,side));
                CREATE TABLE cycles(account TEXT,cycle TEXT,at TEXT,nav REAL,cash REAL,payload TEXT,
                    PRIMARY KEY(account,cycle));
                CREATE TABLE outbox(id INTEGER PRIMARY KEY,trade_id INTEGER UNIQUE,payload TEXT,
                    delivered_at TEXT,attempts INTEGER NOT NULL DEFAULT 0,next_attempt_at TEXT);
                CREATE TABLE state(key TEXT PRIMARY KEY,payload TEXT);
            """)
            db.executemany("INSERT INTO accounts VALUES(?,?,?)", [(name, initial_cash, initial_cash) for name in ACCOUNTS])
        db.close()
        self.path.chmod(0o600)

    def state(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key: str, value) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, json.dumps(value, allow_nan=False)))

    def holdings(self, account: str) -> dict:
        with self.connect() as db:
            return {row["symbol"]: dict(row) for row in db.execute("SELECT * FROM positions WHERE account=?", (account,))}

    def summary(self) -> dict:
        with self.connect() as db:
            result = {}
            for account in ACCOUNTS:
                row = dict(db.execute("SELECT * FROM accounts WHERE id=?", (account,)).fetchone())
                positions = [dict(item) for item in db.execute("SELECT * FROM positions WHERE account=?", (account,))]
                row["positions"] = positions
                row["equity"] = row["cash"] + sum(p["qty"] * p["mark"] for p in positions)
                row["return_pct"] = (row["equity"] / row["initial_cash"] - 1) * 100
                row["trades"] = db.execute("SELECT COUNT(*) FROM trades WHERE account=?", (account,)).fetchone()[0]
                row["realized_pnl"] = db.execute("SELECT COALESCE(SUM(realized),0) FROM trades WHERE account=?", (account,)).fetchone()[0]
                row["fees"] = db.execute("SELECT COALESCE(SUM(fee),0) FROM trades WHERE account=?", (account,)).fetchone()[0]
                curve = db.execute("SELECT nav FROM cycles WHERE account=? ORDER BY at", (account,)).fetchall()
                peak, drawdown = row["initial_cash"], 0.0
                for point in curve:
                    peak = max(peak, point["nav"])
                    drawdown = max(drawdown, 1 - point["nav"] / peak)
                row["max_drawdown_pct"] = drawdown * 100
                row["latest_cycle"] = self._latest_cycle(db, account)
                row["equity_provisional"] = bool(row["latest_cycle"] and row["latest_cycle"].get("stale_marks"))
                result[account] = row
        return {"strategy": "stock_alpha", "mode": "paper_only", "official_account": "C_ai", "accounts": result}

    def mark_close(self, day: str, closes: dict[str, float], factors: dict[str, float]):
        at = datetime.strptime(day, "%Y%m%d").replace(hour=15, tzinfo=TZ).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for account in ACCOUNTS:
                stale = []
                positions = db.execute("SELECT * FROM positions WHERE account=?", (account,)).fetchall()
                for p in positions:
                    price, factor = closes.get(p["symbol"]), factors.get(p["symbol"])
                    if (not price or not math.isfinite(price) or price <= 0 or not factor or not p["adj_factor"]
                            or abs(factor / p["adj_factor"] - 1) > 1e-7):
                        stale.append(p["symbol"])
                        continue
                    db.execute("UPDATE positions SET mark=?,mark_at=? WHERE account=? AND symbol=?", (price, at, account, p["symbol"]))
                cash = db.execute("SELECT cash FROM accounts WHERE id=?", (account,)).fetchone()[0]
                nav = cash + db.execute("SELECT COALESCE(SUM(qty*mark),0) FROM positions WHERE account=?", (account,)).fetchone()[0]
                result = {"status": "close_valuation", "at": at, "nav": nav, "cash": cash, "stale_marks": stale}
                db.execute("INSERT OR REPLACE INTO cycles VALUES(?,?,?,?,?,?)", (account, day + "close", at, nav, cash, json.dumps(result)))

    @staticmethod
    def _latest_cycle(db, account):
        row = db.execute("SELECT payload FROM cycles WHERE account=? ORDER BY at DESC LIMIT 1", (account,)).fetchone()
        return json.loads(row[0]) if row else None

    def execute(self, account: str, plan: dict, quotes: dict[str, Quote], now: datetime,
                *, factors: dict[str, float], blocked: set[str] | None = None,
                corporate_actions: set[str] | None = None) -> dict:
        if account not in ACCOUNTS or now.tzinfo is None:
            raise ValueError("Invalid account or naive clock")
        now = now.astimezone(TZ)
        if now.weekday() >= 5 or not ((9, 30) <= (now.hour, now.minute) < (11, 30)
                                     or (13, 0) <= (now.hour, now.minute) < (14, 57)):
            return {"status": "closed", "no_orders": True}
        weights = plan.get("weights", {})
        if (len(weights) > 12 or any(not re.fullmatch(r"(?:60\d{4}\.SH|68\d{4}\.SH|00\d{4}\.SZ|30\d{4}\.SZ)", s) for s in weights)
                or any(not math.isfinite(w) or not 0 <= w <= 0.08000001 for w in weights.values())
                or sum(weights.values()) > 0.80000001):
            raise ValueError("Plan violates frozen portfolio limits")
        cycle = now.strftime("%Y%m%d%H") + f"{now.minute // 2 * 2:02d}"
        day, at = now.strftime("%Y%m%d"), now.isoformat()
        blocked = blocked or set()
        result = {"account": account, "cycle": cycle, "at": at, "orders": [], "blocked": [],
                  "ai_mode": plan.get("ai_mode", "rules_only"), "mode": "paper_only"}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old_cycle = db.execute("SELECT payload FROM cycles WHERE account=? AND cycle=?", (account, cycle)).fetchone()
            if old_cycle:
                return {**json.loads(old_cycle[0]), "duplicate_cycle": True}
            cash = db.execute("SELECT cash FROM accounts WHERE id=?", (account,)).fetchone()[0]
            positions = {p["symbol"]: dict(p) for p in db.execute("SELECT * FROM positions WHERE account=?", (account,))}
            corporate_pending = set(corporate_actions or set()) & set(positions)
            stale = []
            for symbol, p in positions.items():
                factor = factors.get(symbol)
                if factor and p["adj_factor"] and abs(factor / p["adj_factor"] - 1) > 1e-7:
                    corporate_pending.add(symbol)
                quote = quotes.get(symbol)
                if quote and quote.fresh(now) and symbol not in corporate_pending:
                    p["mark"], p["mark_at"] = quote.price, quote.at.isoformat()
                    db.execute("UPDATE positions SET mark=?,mark_at=? WHERE account=? AND symbol=?",
                               (p["mark"], p["mark_at"], account, symbol))
                else:
                    stale.append(symbol)
            nav = cash + sum(p["qty"] * p["mark"] for p in positions.values())
            if not math.isfinite(nav) or nav <= 0:
                raise RuntimeError("Invalid account valuation")
            result["stale_marks"] = stale
            result["corporate_action_pending"] = sorted(corporate_pending)
            plan_id = str(plan.get("plan_id") or "no_plan")
            signals = []
            for symbol in sorted(set(weights) | set(positions)):
                p, q = positions.get(symbol), quotes.get(symbol)
                quantity = p["qty"] if p else 0
                hard_stop = bool(p and q and q.fresh(now) and q.price <= p["cost"] * 0.92)
                if hard_stop:
                    signals.append(("SELL", symbol, quantity, "hard_stop_8pct"))
                    continue
                if not plan.get("ready") or not q or not q.fresh(now):
                    if symbol in weights and weights[symbol] > 0:
                        result["blocked"].append({"symbol": symbol, "reason": "plan_or_quote_unavailable"})
                    continue
                if db.execute("SELECT 1 FROM trades WHERE account=? AND plan_id=? AND symbol=? LIMIT 1",
                              (account, plan_id, symbol)).fetchone():
                    continue
                target_value = nav * weights.get(symbol, 0)
                delta_value = target_value - quantity * q.price
                if quantity and weights.get(symbol, 0) > 0 and abs(delta_value) < nav * 0.01:
                    continue
                if delta_value < 0:
                    reduction = quantity if weights.get(symbol, 0) == 0 else min(quantity, int(-delta_value / q.price / 100) * 100)
                    if reduction:
                        signals.append(("SELL", symbol, reduction, "rebalance"))
                elif delta_value > 0:
                    signals.append(("BUY", symbol, delta_value, "rebalance"))
            signals.sort(key=lambda item: (item[0] != "SELL", item[1]))
            for side, symbol, wanted, reason in signals:
                q = quotes.get(symbol)
                error = None
                if symbol in corporate_pending:
                    error = "corporate_action_reconciliation_required"
                elif not q or not q.fresh(now):
                    error = "stale_or_missing_quote"
                elif (not all(math.isfinite(v) for v in (q.up_limit, q.down_limit, q.volume, q.bid, q.ask))
                      or q.up_limit <= q.down_limit or q.down_limit <= 0):
                    error = "missing_official_price_limits"
                elif side == "BUY" and (symbol in blocked or stale):
                    error = "instrument_blocked_or_incomplete_valuation"
                elif side == "BUY" and (not factors.get(symbol) or not math.isfinite(factors[symbol]) or factors[symbol] <= 0
                                        or re.match(r"^\*?ST", q.name.upper())):
                    error = "missing_adjustment_factor_or_name_risk"
                elif side == "BUY" and db.execute("SELECT 1 FROM trades WHERE account=? AND symbol=? AND side='SELL' AND substr(at,1,10)=?",
                                                   (account, symbol, now.date().isoformat())).fetchone():
                    error = "no_same_day_reentry"
                elif side == "BUY" and symbol not in positions and len(positions) >= 12:
                    error = "position_count_limit"
                elif q.volume <= 0:
                    error = "no_confirmed_volume"
                elif side == "BUY" and (q.ask <= 0 or q.price >= q.up_limit * 0.995):
                    error = "no_offer_or_near_limit_up"
                elif side == "SELL" and (q.bid <= 0 or q.price <= q.down_limit * 1.005):
                    error = "no_bid_or_near_limit_down"
                if error:
                    result["blocked"].append({"symbol": symbol, "reason": error})
                    db.execute("INSERT OR IGNORE INTO attempts VALUES(?,?,?,?,?,?)", (account, cycle, symbol, side, "blocked", error))
                    continue
                price = round((max(q.price, q.ask) * 1.001 if side == "BUY" else min(q.price, q.bid) * 0.999), 2)
                if not q.down_limit <= price <= q.up_limit:
                    result["blocked"].append({"symbol": symbol, "reason": "slippage_outside_price_limits"})
                    continue
                consumed = db.execute("SELECT COALESCE(SUM(qty),0) FROM trades WHERE account=? AND symbol=? AND substr(at,1,10)=?",
                                      (account, symbol, now.date().isoformat())).fetchone()[0]
                capacity = max(0, int(q.volume * 0.01) - consumed)
                p = positions.get(symbol)
                if side == "BUY":
                    exposure = sum(item["qty"] * item["mark"] for item in positions.values())
                    budget = min(wanted, cash, max(0, nav * 0.8 - exposure),
                                 max(0, nav * 0.08 - (p["qty"] * q.price if p else 0)))
                    quantity = min(int(max(0, budget - 5) / (price * 1.0005)), capacity)
                    minimum, step = (200, 1) if symbol.startswith("68") else (100, 100)
                    quantity = min(quantity, 100_000 if symbol.startswith("68") else 1_000_000)
                    quantity = quantity // step * step
                    if quantity < minimum:
                        result["blocked"].append({"symbol": symbol, "reason": "cash_target_or_volume_below_lot"})
                        continue
                    fee = round(max(5, quantity * price * 0.0005), 2)
                    debit = quantity * price + fee
                    if debit > cash + 1e-7:
                        raise RuntimeError("Cash reservation failure")
                    cash -= debit
                    old_qty = p["qty"] if p else 0
                    cost = ((p["cost"] * old_qty if p else 0) + debit) / (old_qty + quantity)
                    updated = {"account": account, "symbol": symbol, "name": q.name, "qty": old_qty + quantity,
                               "cost": cost, "mark": q.price, "mark_at": q.at.isoformat(),
                               "entry_date": p["entry_date"] if p else day, "adj_factor": factors.get(symbol, 0)}
                    if not updated["adj_factor"]:
                        raise RuntimeError("Missing entry adjustment factor")
                    positions[symbol] = updated
                    db.execute("INSERT OR REPLACE INTO positions VALUES(:account,:symbol,:name,:qty,:cost,:mark,:mark_at,:entry_date,:adj_factor)", updated)
                    db.execute("INSERT INTO lots VALUES(?,?,?,?) ON CONFLICT(account,symbol,day) DO UPDATE SET qty=qty+excluded.qty",
                               (account, symbol, day, quantity))
                    realized = 0
                else:
                    available = db.execute("SELECT COALESCE(SUM(qty),0) FROM lots WHERE account=? AND symbol=? AND day<?",
                                           (account, symbol, day)).fetchone()[0]
                    quantity = min(int(wanted), available, capacity)
                    if quantity < p["qty"]:
                        if symbol.startswith("68"):
                            quantity = quantity if quantity >= 200 else 0
                        else:
                            quantity = quantity // 100 * 100
                    if quantity <= 0:
                        result["blocked"].append({"symbol": symbol, "reason": "t_plus_one_or_volume_limit"})
                        continue
                    fee = round(max(5, quantity * price * 0.0005) + quantity * price * 0.0005, 2)
                    cash += quantity * price - fee
                    realized = quantity * (price - p["cost"]) - fee
                    remaining = quantity
                    for lot in db.execute("SELECT day,qty FROM lots WHERE account=? AND symbol=? AND day<? ORDER BY day", (account, symbol, day)).fetchall():
                        used = min(remaining, lot["qty"])
                        db.execute("UPDATE lots SET qty=qty-? WHERE account=? AND symbol=? AND day=?", (used, account, symbol, lot["day"]))
                        remaining -= used
                        if remaining == 0:
                            break
                    p["qty"] -= quantity
                    if p["qty"]:
                        db.execute("UPDATE positions SET qty=? WHERE account=? AND symbol=?", (p["qty"], account, symbol))
                    else:
                        db.execute("DELETE FROM positions WHERE account=? AND symbol=?", (account, symbol))
                        del positions[symbol]
                    db.execute("DELETE FROM lots WHERE qty=0")
                order = {"symbol": symbol, "name": q.name, "side": side, "quantity": quantity, "price": price,
                         "fee": fee, "realized_pnl": realized, "reason": reason, "at": at,
                         "ai_mode": result["ai_mode"], "strategy": "stock_alpha", "account": account}
                cursor = db.execute("INSERT INTO trades(account,cycle,plan_id,symbol,name,side,qty,price,fee,realized,reason,at,ai_mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (account, cycle, plan_id, symbol, q.name, side, quantity, price, fee, realized, reason, at, result["ai_mode"]))
                order["trade_id"] = cursor.lastrowid
                result["orders"].append(order)
                if account == "C_ai":
                    db.execute("INSERT INTO outbox(trade_id,payload) VALUES(?,?)", (cursor.lastrowid, json.dumps(order, ensure_ascii=False)))
            if cash < -1e-6:
                raise RuntimeError("Negative cash")
            db.execute("UPDATE accounts SET cash=? WHERE id=?", (cash, account))
            result["cash"] = cash
            result["nav"] = cash + sum(p["qty"] * p["mark"] for p in positions.values())
            result["status"] = "valuation_incomplete" if stale else "ok"
            db.execute("INSERT INTO cycles VALUES(?,?,?,?,?,?)", (account, cycle, at, result["nav"], cash, json.dumps(result, allow_nan=False)))
        return result
