from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from etf_strategy.config import load_settings
from etf_strategy.tushare_client import TuShareETFClient


def replay_reports(
    report_paths: list[Path],
    initial_cash: float = 1_000_000.0,
    cost_bps_per_side: float = 10.0,
    entry_time: str = "09:31:00",
    final_mark_time: str = "14:59:00",
) -> dict[str, Any]:
    """Replay daily strategy orders at actual 1-minute prices.

    Signals remain those generated without future intraday data. Only order
    pricing and the carried cash/position state are recomputed at one-minute
    resolution, making this an execution-quality replay rather than a new
    intraday alpha model.
    """
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in report_paths]
    events = _events(payloads)
    if not events:
        raise ValueError("No daily backtest trades were found in the supplied reports.")

    settings = load_settings()
    client = TuShareETFClient(settings)
    cost_rate = max(cost_bps_per_side, 0.0) / 10_000.0
    cash = float(initial_cash)
    positions: dict[str, int] = defaultdict(int)
    fills: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    cache: dict[tuple[str, str, str], float] = {}

    for event in events:
        symbol = str(event["symbol"])
        trade_date = str(event["date"])
        side = str(event["side"])
        price = _minute_price(client, cache, symbol, trade_date, entry_time)
        if price <= 0:
            continue
        if side == "SELL":
            quantity = positions.pop(symbol, 0)
            if quantity <= 0:
                continue
            cash += quantity * price * (1 - cost_rate)
        else:
            # Annual daily reports are independently capital-reset. When they
            # are replayed as one continuous account, a new BUY must first
            # close any prior target that the reset report could not see.
            for held_symbol, held_quantity in list(positions.items()):
                if held_symbol == symbol or held_quantity <= 0:
                    continue
                held_price = _minute_price(client, cache, held_symbol, trade_date, entry_time)
                if held_price > 0:
                    cash += held_quantity * held_price * (1 - cost_rate)
                    fills.append({"date": trade_date, "time": entry_time, "side": "SELL", "symbol": held_symbol, "quantity": held_quantity, "price": held_price, "reason": "continuous_replay_switch"})
                positions.pop(held_symbol, None)
            desired = int(event["quantity"])
            quantity = min(desired, int(cash / (price * (1 + cost_rate)) // 100 * 100))
            if quantity <= 0:
                continue
            cash -= quantity * price * (1 + cost_rate)
            positions[symbol] += quantity
        fills.append({"date": trade_date, "time": entry_time, "side": side, "symbol": symbol, "quantity": quantity, "price": price})
        marked_equity = cash + sum(
            held_quantity * _minute_price(client, cache, held_symbol, trade_date, entry_time)
            for held_symbol, held_quantity in positions.items()
        )
        equity_curve.append(
            {
                "timestamp": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]} {entry_time}",
                "equity": round(marked_equity, 2),
                "symbol": next(iter(positions), "CASH"),
            }
        )

    end_date = max(str(item.get("end_date") or "") for item in payloads)
    ending_equity = cash
    marks: list[dict[str, Any]] = []
    for symbol, quantity in positions.items():
        price = _minute_price(client, cache, symbol, end_date, final_mark_time)
        ending_equity += quantity * price
        marks.append({"symbol": symbol, "quantity": quantity, "price": price})
    equity_curve.append(
        {
            "timestamp": f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]} {final_mark_time}",
            "equity": round(ending_equity, 2),
            "symbol": next(iter(positions), "CASH"),
        }
    )

    return {
        "mode": "one_minute_execution_replay",
        "start_date": min(str(item.get("start_date") or "") for item in payloads),
        "end_date": end_date,
        "initial_cash": initial_cash,
        "ending_equity": round(ending_equity, 2),
        "total_return_pct": round((ending_equity / initial_cash - 1) * 100, 4),
        "fill_count": len(fills),
        "cost_bps_per_side": cost_bps_per_side,
        "entry_time": entry_time,
        "final_marks": marks,
        "fills": fills,
        "equity_curve": equity_curve,
    }


def _events(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for payload in payloads:
        for trade in payload.get("trades") or []:
            if trade.get("side") in {"BUY", "SELL"} and trade.get("date") and trade.get("symbol"):
                output.append(dict(trade))
    return sorted(output, key=lambda item: (str(item["date"]), 0 if item["side"] == "SELL" else 1, str(item["symbol"])))


def _minute_price(
    client: TuShareETFClient,
    cache: dict[tuple[str, str, str], float],
    symbol: str,
    trade_date: str,
    time_of_day: str,
) -> float:
    key = (symbol, trade_date, time_of_day)
    if key in cache:
        return cache[key]
    bars = client.fetch_historical_minutes(
        symbol,
        f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]} 09:30:00",
        f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]} 15:01:00",
        freq="1min",
    )
    target = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]} {time_of_day}"
    selected = bars[bars["trade_time"].astype(str) >= target]
    price = float(selected.iloc[0]["close"]) if not selected.empty else 0.0
    cache[key] = price
    return price


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay daily ETF orders using historical 1-minute execution prices")
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0)
    parser.add_argument("--cost-bps-per-side", type=float, default=10.0)
    args = parser.parse_args(argv)
    result = replay_reports(args.reports, args.initial_cash, args.cost_bps_per_side)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
