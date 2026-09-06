"""Isolated, resumable TuShare research snapshots; never opens a paper account."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class ResearchDataClient:
    def __init__(self, directory: Path, *, offline: bool = False) -> None:
        self.directory = directory
        self.offline = offline
        self.session = requests.Session()
        self.session.trust_env = False
        self.request_count = 0
        self.cache_hits = 0

    def query(self, api: str, *, fields: str = "", **params: object) -> pd.DataFrame:
        request = {"api_name": api, "params": params, "fields": fields}
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        path = self.directory / "requests" / f"{api}_{key}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))["data"]
            self.cache_hits += 1
        else:
            if self.offline:
                raise RuntimeError(f"Missing offline input: {path.name}")
            token = os.environ.get("TUSHARE_TOKEN", "")
            if not token:
                raise RuntimeError("TUSHARE_TOKEN is required; no synthetic data fallback")
            endpoint = os.environ.get("TUSHARE_BASE_URL") or "https://api.tushare.pro"
            error = "unavailable"
            for attempt in range(3):
                try:
                    response = self.session.post(endpoint, json={**request, "token": token}, timeout=(10, 30))
                    self.request_count += 1
                    response.raise_for_status()
                    body = response.json()
                    if body.get("code") != 0 or not isinstance(body.get("data"), dict):
                        raise ValueError(f"provider_code={body.get('code')}")
                    data = body["data"]
                    if not isinstance(data.get("items"), list) or not isinstance(data.get("fields"), list):
                        raise ValueError("invalid_table")
                    write_json(path, {"request": request, "captured_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "data": data})
                    time.sleep(0.12)
                    break
                except (requests.RequestException, ValueError) as exc:
                    # Provider exception text can contain credentials or request URLs.
                    error = type(exc).__name__
                    time.sleep(attempt + 1)
            else:
                raise RuntimeError(f"{api} failed after 3 attempts ({error}); credentials redacted")
        frame = pd.DataFrame(data["items"], columns=data["fields"])
        if len(frame) >= 6000:
            raise RuntimeError(f"{api} returned >=6000 rows: possible provider truncation; split the request")
        return frame


def build_dataset(directory: Path, start: str, end: str, universe_limit: int, *, offline: bool = False) -> dict:
    if not 20 <= universe_limit <= 800:
        raise ValueError("universe_limit must be between 20 and 800")
    datetime.strptime(start, "%Y%m%d")
    datetime.strptime(end, "%Y%m%d")
    if start > end:
        raise ValueError("start must not exceed end")
    directory.mkdir(parents=True, exist_ok=True)
    client = ResearchDataClient(directory, offline=offline)
    warmup = f"{int(start[:4]) - 1}0101"
    calendar_frame = client.query("trade_cal", exchange="SSE", start_date=warmup, end_date=end, is_open="1")
    calendar = sorted(calendar_frame["cal_date"].astype(str).unique().tolist())
    if not calendar or calendar[-1] < start:
        raise RuntimeError("No verified exchange sessions in requested range")

    memberships = []
    universe_dates = []
    for year in range(int(start[:4]), int(end[:4]) + 1):
        known_dates = [day for day in calendar if day < f"{year}0101"]
        if not known_dates:
            raise RuntimeError(f"No pre-year membership date available for {year}")
        day = known_dates[-1]
        snapshot = client.query("daily_basic", trade_date=day,
                                fields="ts_code,trade_date,close,total_mv,circ_mv,turnover_rate,pe_ttm,pb")
        required = {"ts_code", "circ_mv", "turnover_rate", "close"}
        if not required.issubset(snapshot.columns) or snapshot.empty:
            raise RuntimeError(f"Incomplete historical universe at {day}")
        for field in ["circ_mv", "turnover_rate", "close"]:
            snapshot[field] = pd.to_numeric(snapshot[field], errors="coerce")
        snapshot = snapshot[snapshot.ts_code.str.match(r"^(60|68|00|30)\d{4}\.(SH|SZ)$")]
        snapshot = snapshot[(snapshot.circ_mv > 0) & (snapshot.turnover_rate > 0) & (snapshot.close > 0)]
        # Fixed historical large/liquid universe, not today's surviving winners.
        selected = snapshot.sort_values(["circ_mv", "ts_code"], ascending=[False, True]).head(universe_limit)
        memberships.extend({"ts_code": symbol, "trade_date": day} for symbol in selected.ts_code)
        universe_dates.append({"year": year, "known_at": day, "names": len(selected)})
    symbols = sorted({item["ts_code"] for item in memberships})
    bars, financials, failures = [], [], []
    for index, symbol in enumerate(symbols, 1):
        try:
            daily = client.query("daily", ts_code=symbol, start_date=warmup, end_date=end)
            factors = client.query("adj_factor", ts_code=symbol, start_date=warmup, end_date=end)
            basic = client.query("daily_basic", ts_code=symbol, start_date=warmup, end_date=end,
                                 fields="ts_code,trade_date,pe_ttm,pb")
            financial = client.query("fina_indicator", ts_code=symbol, start_date=warmup, end_date=end,
                                     fields="ts_code,ann_date,end_date,roe,or_yoy,ocfps,debt_to_assets")
            if daily.empty or factors.empty or basic.empty or financial.empty:
                raise RuntimeError("required_endpoint_empty")
            for table in [daily, factors, basic]:
                table["trade_date"] = table.trade_date.astype(str)
                if table.duplicated(["ts_code", "trade_date"]).any():
                    raise RuntimeError("ambiguous_price_rows")
            merged = daily.merge(factors[["ts_code", "trade_date", "adj_factor"]],
                                 on=["ts_code", "trade_date"], how="left", validate="one_to_one")
            merged = merged.merge(basic, on=["ts_code", "trade_date"], how="left", validate="one_to_one")
            bars.append(merged)
            financials.append(financial)
        except RuntimeError as exc:
            failures.append({"symbol": symbol, "error": str(exc)})
        write_json(directory / "progress.json", {
            "stage": "fetch", "completed": index, "total": len(symbols), "failed": len(failures),
            "requests": client.request_count, "cache_hits": client.cache_hits,
        })
        if index % 10 == 0 or index == len(symbols):
            print(json.dumps({"completed": index, "total": len(symbols), "failed": len(failures)}), flush=True)
    if not bars:
        raise RuntimeError("No complete research symbols; see progress and endpoint results")
    paths = {}
    for name, frame in [("bars", pd.concat(bars, ignore_index=True)),
                        ("fundamentals", pd.concat(financials, ignore_index=True)),
                        ("memberships", pd.DataFrame(memberships))]:
        path = directory / f"{name}.csv.gz"
        frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
        paths[name] = {"rows": len(frame), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    write_json(directory / "calendar.json", calendar)
    paths["calendar"] = {"rows": len(calendar), "sha256": hashlib.sha256((directory / "calendar.json").read_bytes()).hexdigest()}
    manifest = {
        "version": "stock_alpha_research_v1", "start": start, "end": end,
        "last_exchange_session": calendar[-1], "universe_limit": universe_limit,
        "universe": "annual_previous_close_largest_freefloat_liquid_a_shares",
        "requested_symbols": len(symbols), "complete_symbols": len(bars),
        "universe_dates": universe_dates, "failures": failures, "files": paths,
        "requests": client.request_count, "cache_hits": client.cache_hits,
        "production_eligible": False,
        "limitations": [
            "Annual historical large-cap pool, not all A-shares or the legacy adaptive160 universe",
            "Financial announcements lagged but provider revision vintages are not independently verified",
            "No historical ST/delisting-status, industry, auction queue, corporate-action cash/share ledger",
            "Adjustment-factor total-return proxy is not a broker-executable share ledger",
            "Historical years previously researched in this project are not untouched out-of-sample data",
            "No production promotion from these exploratory results",
        ],
        "captured_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    }
    write_json(directory / "manifest.json", manifest)
    write_json(directory / "progress.json", {"stage": "fetch_complete", "completed": len(symbols), "total": len(symbols)})
    return manifest


def load_dataset(directory: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str], dict]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    frames = []
    for name in ["bars", "fundamentals", "memberships"]:
        path = directory / f"{name}.csv.gz"
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files"][name]["sha256"]:
            raise ValueError(f"Input hash mismatch: {name}")
        frames.append(pd.read_csv(path, dtype={"trade_date": str, "ts_code": str, "ann_date": str, "end_date": str}))
    calendar_path = directory / "calendar.json"
    if hashlib.sha256(calendar_path.read_bytes()).hexdigest() != manifest["files"]["calendar"]["sha256"]:
        raise ValueError("Input hash mismatch: calendar")
    calendar = json.loads(calendar_path.read_text(encoding="utf-8"))
    return *frames, calendar, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--start", default="20180101")
    parser.add_argument("--end", required=True)
    parser.add_argument("--universe-limit", type=int, default=160)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    manifest = build_dataset(args.directory, args.start, args.end, args.universe_limit, offline=args.offline)
    print(json.dumps(manifest, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
