from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from etf_strategy.runner import ETFStrategyRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local-only Wufu ETF research strategy")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    refresh = subparsers.add_parser("refresh", help="Fetch ETF universe and daily data into local SQLite")
    refresh.add_argument("--as-of", help="YYYYMMDD; defaults to today")
    refresh.add_argument("--lookback-calendar-days", type=int, default=120)
    refresh.add_argument("--force", action="store_true", help="Refetch already cached daily snapshots")

    decide = subparsers.add_parser("decide", help="Generate a local ETF trade plan from cached data")
    _add_decision_args(decide)

    run = subparsers.add_parser("run", help="Refresh then generate a local ETF trade plan")
    _add_decision_args(run)

    backtest = subparsers.add_parser("backtest", help="Replay cached daily data with next-open execution")
    backtest.add_argument("--start-date", required=True, help="YYYYMMDD")
    backtest.add_argument("--end-date", required=True, help="YYYYMMDD")
    backtest.add_argument("--initial-cash", type=float, default=1_000_000.0)
    backtest.add_argument("--cost-bps-per-side", type=float, default=10.0)

    wufu_v7 = subparsers.add_parser(
        "backtest-wufu-v7",
        help="Replay the source Wufu V7 static ETF strategy with 1-minute data",
    )
    wufu_v7.add_argument("--start-date", required=True, help="YYYYMMDD")
    wufu_v7.add_argument("--end-date", required=True, help="YYYYMMDD")
    wufu_v7.add_argument("--initial-cash", type=float, default=1_000_000.0)
    wufu_v7.add_argument("--fetch-minutes", action="store_true", help="Fetch missing historical 1-minute ETF windows before replay")
    wufu_v7.add_argument("--minute-fetch-workers", type=int, default=4, help="Parallel TuShare minute requests, capped at 8")
    return parser


def _add_decision_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--as-of", help="YYYYMMDD; defaults to the latest local ETF daily bar")
    parser.add_argument("--account-value", type=float, default=1_000_000.0)
    parser.add_argument("--current-symbol", help="Existing ETF symbol for a switch/hold plan")
    parser.add_argument("--confirm-intraday", action="store_true", help="Require current 1-minute ETF trend confirmation")
    parser.add_argument("--enrich-nav", action="store_true", help="Fetch the selected ETF NAV and reject excessive premium")
    parser.add_argument("--with-ai", action="store_true", help="Enable optional MiniMax veto review; needs ETF_MINIMAX_ENABLED=true")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    runner = ETFStrategyRunner()
    if args.command == "refresh":
        result = runner.refresh(args.as_of, args.lookback_calendar_days, args.force)
    elif args.command == "decide":
        result = runner.decide(
            as_of=args.as_of,
            account_value=args.account_value,
            current_symbol=args.current_symbol,
            confirm_intraday=args.confirm_intraday,
            enrich_nav=args.enrich_nav,
            with_ai=args.with_ai,
        )
    elif args.command == "run":
        result = runner.run(
            as_of=args.as_of,
            account_value=args.account_value,
            current_symbol=args.current_symbol,
            confirm_intraday=args.confirm_intraday,
            enrich_nav=args.enrich_nav,
            with_ai=args.with_ai,
        )
    elif args.command == "backtest":
        result = runner.backtest(
            start_date=args.start_date,
            end_date=args.end_date,
            initial_cash=args.initial_cash,
            cost_bps_per_side=args.cost_bps_per_side,
        )
    else:
        result = runner.backtest_wufu_v7(
            start_date=args.start_date,
            end_date=args.end_date,
            initial_cash=args.initial_cash,
            fetch_minutes=args.fetch_minutes,
            minute_fetch_workers=args.minute_fetch_workers,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
