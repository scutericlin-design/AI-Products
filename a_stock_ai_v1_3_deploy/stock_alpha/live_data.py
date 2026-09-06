"""Isolated cached provider snapshots with explicit availability and quote timestamps."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import time

import pandas as pd
import requests

from stock_alpha.data import load_dataset, write_json
from stock_alpha.paper import Quote, TZ


class LiveData:
    def __init__(self, directory: Path, seed: Path, config: dict):
        self.directory, self.seed, self.config = directory, seed, config
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": "stock-alpha-paper/1"})
        self.directory.mkdir(parents=True, exist_ok=True)
        self.last_request = 0.0

    def query(self, api: str, *, cache: str | None = None, fields: str = "", **params) -> pd.DataFrame:
        request = {"api_name": api, "params": params, "fields": fields}
        path = None
        if cache:
            digest = hashlib.sha256(json.dumps([cache, request], sort_keys=True).encode()).hexdigest()
            path = self.directory / "requests" / f"{api}_{digest}.json"
            if path.exists():
                value = json.loads(path.read_text())
                return pd.DataFrame(value["data"]["items"], columns=value["data"]["fields"])
        market = self.config["market"]
        error = "unavailable"
        for attempt in range(2):
            time.sleep(max(0, 0.25 - (time.monotonic() - self.last_request)))
            try:
                self.last_request = time.monotonic()
                response = self.session.post(market["url"], json={**request, "token": market["token"]},
                                             timeout=(10, 35), allow_redirects=False)
                response.raise_for_status()
                body = response.json()
                data = body.get("data")
                if body.get("code") != 0 or not isinstance(data, dict):
                    raise ValueError("provider_rejected_query")
                if not isinstance(data.get("items"), list) or not isinstance(data.get("fields"), list):
                    raise ValueError("invalid_provider_table")
                if len(data["items"]) >= 6000:
                    raise ValueError("possible_provider_truncation")
                if path:
                    write_json(path, {"request": request, "data": data,
                                      "received_at": datetime.now(TZ).isoformat()})
                return pd.DataFrame(data["items"], columns=data["fields"])
            except (requests.RequestException, ValueError, TypeError) as exc:
                error = type(exc).__name__
                if attempt == 0:
                    time.sleep(1)
        raise RuntimeError(f"{api}:{error}; provider details redacted")

    def calendar(self, now: datetime) -> list[str]:
        day = now.astimezone(TZ).strftime("%Y%m%d")
        frame = self.query("trade_cal", cache=day, exchange="SSE", start_date=f"{now.year - 1}0101",
                           end_date=f"{now.year + 1}1231")
        if frame.empty or not {"cal_date", "is_open"}.issubset(frame.columns):
            raise RuntimeError("Missing exchange calendar")
        return sorted(frame.loc[frame.is_open.astype(str) == "1", "cal_date"].astype(str).tolist())

    def snapshot(self, now: datetime) -> dict:
        today = now.astimezone(TZ).strftime("%Y%m%d")
        calendar = self.calendar(now)
        previous = [day for day in calendar if day < today]
        if not previous:
            raise RuntimeError("No confirmed prior exchange session")
        as_of = previous[-1]
        cached = self.directory / "snapshots" / as_of
        if (cached / "complete.json").exists():
            return self._read_snapshot(cached, calendar)
        bars, financials, memberships, _, manifest = load_dataset(self.seed)
        if manifest["end"] > as_of:
            raise RuntimeError("Seed is later than decision cutoff")
        membership_day = str(memberships.loc[memberships.trade_date <= as_of, "trade_date"].max())
        symbols = sorted(memberships.loc[memberships.trade_date == membership_day, "ts_code"].unique())
        if len(symbols) != 160:
            raise RuntimeError("Frozen research universe must contain 160 names")
        # Freeze the initial pool for the forward experiment; do not silently rotate at year end.
        bars = bars[bars.ts_code.isin(symbols)].sort_values(["ts_code", "trade_date"])
        bars = bars.groupby("ts_code", group_keys=False).tail(300).copy()
        latest = str(bars.trade_date.max())
        for day in [d for d in calendar if latest < d <= as_of]:
            daily = self.query("daily", cache=day, trade_date=day)
            adjustment = self.query("adj_factor", cache=day, trade_date=day)
            basic = self.query("daily_basic", cache=day, trade_date=day, fields="ts_code,trade_date,pe_ttm,pb")
            if any(frame.empty for frame in (daily, adjustment, basic)):
                raise RuntimeError(f"Missing completed-session data:{day}")
            daily = daily[daily.ts_code.isin(symbols)]
            merged = daily.merge(adjustment[["ts_code", "trade_date", "adj_factor"]], on=["ts_code", "trade_date"], validate="one_to_one")
            merged = merged.merge(basic, on=["ts_code", "trade_date"], validate="one_to_one")
            bars = pd.concat([bars, merged], ignore_index=True)
        metadata = self.query("stock_basic", cache=today, exchange="", list_status="L",
                              fields="ts_code,name,industry,list_date")
        if metadata.empty:
            raise RuntimeError("Missing current stock metadata")
        collected, failures = [], []
        for index, symbol in enumerate(symbols):
            try:
                frame = self.query("fina_indicator", cache=today, ts_code=symbol,
                                   start_date=f"{now.year - 2}0101", end_date=today,
                                   fields="ts_code,ann_date,end_date,roe,or_yoy,ocfps,debt_to_assets,roic,ocf_to_or,netprofit_yoy")
                if frame.empty:
                    raise RuntimeError("empty_financial")
                collected.append(frame)
            except RuntimeError:
                failures.append(symbol)
            if (index + 1) % 20 == 0:
                print(json.dumps({"stage": "financial_refresh", "completed": index + 1, "total": 160,
                                  "failed": len(failures)}), flush=True)
        # Fresh-query failures are excluded from decisions, not replaced by an old favorable report.
        financials = pd.concat(collected, ignore_index=True) if collected else pd.DataFrame()
        if financials.empty or len(failures) > 32:
            raise RuntimeError("Financial data coverage below 80 percent")
        bars["trade_date"] = bars.trade_date.astype(str)
        bars = bars[bars.trade_date <= as_of].sort_values(["ts_code", "trade_date"]).groupby("ts_code", group_keys=False).tail(300)
        bars = bars.drop_duplicates(["ts_code", "trade_date"], keep="last")
        for col in ("ann_date", "end_date"):
            financials[col] = financials[col].astype(str)
        cached.mkdir(parents=True, exist_ok=True)
        for name, frame in [("bars", bars), ("financials", financials), ("memberships", memberships), ("metadata", metadata)]:
            frame.to_csv(cached / f"{name}.csv.gz", index=False, compression="gzip")
        write_json(cached / "complete.json", {"as_of": as_of, "known_at": now.isoformat(), "symbols": symbols,
                                              "financial_failures": failures, "universe_policy": "frozen_2026_research_160"})
        return self._read_snapshot(cached, calendar)

    @staticmethod
    def _read_snapshot(path: Path, calendar: list[str]) -> dict:
        info = json.loads((path / "complete.json").read_text())
        data = {name: pd.read_csv(path / f"{name}.csv.gz", dtype={"ts_code": str, "trade_date": str,
                                                               "ann_date": str, "end_date": str, "list_date": str})
                for name in ("bars", "financials", "memberships", "metadata")}
        return {**info, **data, "calendar": calendar}

    def limits(self, day: str, symbols: list[str]) -> dict:
        result = {}
        # The all-instrument table includes funds and can exceed the provider's row cap.
        for symbol in sorted(set(symbols)):
            frame = self.query("stk_limit", cache=day, trade_date=day, ts_code=symbol,
                               fields="ts_code,trade_date,up_limit,down_limit")
            for row in frame.itertuples():
                if str(row.ts_code) == symbol and str(row.trade_date) == day:
                    up, down = float(row.up_limit), float(row.down_limit)
                    if math.isfinite(up) and math.isfinite(down) and up >= down > 0:
                        result[symbol] = (up, down)
        return result

    def events(self, symbols: list[str], now: datetime, financials: pd.DataFrame | None = None,
               cutoff: str | None = None) -> tuple[list[dict], dict]:
        today = now.strftime("%Y%m%d")
        start = (now - timedelta(days=30)).strftime("%Y%m%d")
        records, failures = [], []
        # forecast is an evening-updated source, not an intraday announcement wire.
        for symbol in symbols:
            try:
                frame = self.query("forecast", cache=today, ts_code=symbol, start_date=start, end_date=today)
                for raw in json.loads(frame.to_json(orient="records", force_ascii=False)):
                    ann_date = str(raw.get("ann_date", ""))
                    if not re.fullmatch(r"\d{8}", ann_date) or ann_date >= (cutoff or today):
                        continue
                    low, profit = raw.get("p_change_min"), raw.get("net_profit_min")
                    positive = (isinstance(low, (float, int)) and isinstance(profit, (float, int))
                                and math.isfinite(low) and math.isfinite(profit) and low > 0 and profit > 0)
                    expires = (datetime.strptime(ann_date, "%Y%m%d") + timedelta(days=30)).strftime("%Y%m%d")
                    identity = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()[:24]
                    records.append({"id": identity, "ts_code": symbol, "ann_date": ann_date,
                                    "known_at": now.isoformat(), "expires": expires,
                                    "positive_numeric_evidence": positive, "source": "tushare_forecast",
                                    "source_url": "https://tushare.pro/document/2?doc_id=45", "facts": raw})
            except RuntimeError:
                failures.append(symbol)
        financial_events = financial_release_events(financials, set(symbols), now, cutoff or today)
        records.extend(financial_events)
        return records, {"source": "forecast_and_financial_releases", "intraday_news": False, "checked_symbols": len(symbols),
                         "failed_symbols": failures, "express_enabled": False,
                         "express_reason": "provider_probe_rejected", "event_count": len(records),
                         "financial_event_count": len(financial_events)}

    def quotes(self, symbols: list[str], now: datetime) -> dict[str, Quote]:
        limits = self.limits(now.strftime("%Y%m%d"), symbols)
        result = {}
        for offset in range(0, len(symbols), 50):
            batch = symbols[offset:offset + 50]
            # Sina is the same backup feed used by the existing system, but with bounded I/O and strict timestamps.
            ids = ",".join(symbol[-2:].lower() + symbol[:6] for symbol in batch)
            response = self.session.get("https://hq.sinajs.cn/list=" + ids,
                                        headers={"Referer": "https://finance.sina.com.cn/"}, timeout=(5, 12))
            response.raise_for_status()
            result.update(parse_sina(response.content.decode("gbk"), set(batch), limits))
        return result

    def corporate_actions(self, symbols: list[str], since: str, now: datetime) -> set[str]:
        affected = set()
        today = now.strftime("%Y%m%d")
        for symbol in symbols:
            try:
                frame = self.query("dividend", cache=today, ts_code=symbol,
                                   fields="ts_code,ann_date,div_proc,record_date,ex_date,pay_date,div_listdate,stk_div,cash_div_tax")
                if not frame.empty and "ex_date" in frame:
                    dates = frame.ex_date.fillna("").astype(str)
                    if ((dates > since) & (dates <= today)).any():
                        affected.add(symbol)
            except RuntimeError:
                # An unverified corporate-action state cannot be treated as a clean position.
                affected.add(symbol)
        return affected


def parse_sina(text: str, requested: set[str], limits: dict) -> dict[str, Quote]:
    result = {}
    for line in text.splitlines():
        match = re.fullmatch(r'\s*var hq_str_(sh|sz)(\d{6})=(.*?);?\s*', line)
        if not match:
            continue
        symbol = match.group(2) + "." + match.group(1).upper()
        if symbol not in requested:
            continue
        try:
            raw = json.loads(match.group(3).rstrip(";"))
            row = next(csv.reader([raw]))
            if len(row) < 32:
                continue
            at = datetime.fromisoformat(row[30] + "T" + row[31]).replace(tzinfo=TZ)
            up, down = limits.get(symbol, (0, 0))
            result[symbol] = Quote(symbol, row[0], float(row[3]), at, "sina_timestamped",
                                   float(row[6]), float(row[7]), float(row[8]), up, down)
        except (ValueError, IndexError, TypeError):
            continue
    return result


def financial_release_events(financials: pd.DataFrame | None, symbols: set[str], now: datetime, cutoff: str) -> list[dict]:
    if financials is None or financials.empty:
        return []
    start = (now - timedelta(days=30)).strftime("%Y%m%d")
    eligible = financials[(financials.ts_code.isin(symbols)) & (financials.ann_date < cutoff)
                          & (financials.ann_date >= start) & (financials.end_date <= financials.ann_date)]
    eligible = eligible.sort_values(["end_date", "ann_date"]).drop_duplicates("ts_code", keep="last")
    events = []
    for facts in json.loads(eligible.to_json(orient="records", force_ascii=False)):
        def positive(name):
            value = facts.get(name)
            return isinstance(value, (int, float)) and math.isfinite(value) and value > 0
        ann_date = str(facts["ann_date"])
        evidence = positive("or_yoy") and positive("netprofit_yoy") and positive("ocf_to_or")
        identity = hashlib.sha256(json.dumps(["financial_release", facts], sort_keys=True).encode()).hexdigest()[:24]
        events.append({"id": identity, "ts_code": facts["ts_code"], "ann_date": ann_date,
                       "known_at": now.isoformat(), "expires": (datetime.strptime(ann_date, "%Y%m%d") + timedelta(days=30)).strftime("%Y%m%d"),
                       "source": "tushare_fina_indicator", "source_url": "https://tushare.pro/document/2?doc_id=79",
                       "positive_numeric_evidence": evidence, "facts": {**facts, "event_type": "reported_financials_not_consensus_surprise"}})
    return events
