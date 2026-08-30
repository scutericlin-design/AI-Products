from __future__ import annotations

import argparse
import json

from qgarp_strategy.backtest import run_historical_backtest
from qgarp_strategy.runner import QGARPStrategyRunner
from qgarp_strategy.v3_research import build_v3_plan_from_store, run_v3_factor_diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description="Q-GARP independent A-share local research/paper strategy")
    parser.add_argument("--refresh", action="store_true", help="Refresh Q-GARP's isolated local cache.")
    parser.add_argument("--backfill", action="store_true", help="Backfill 2018+ daily and point-in-time financial history into isolated storage.")
    parser.add_argument("--hydrate-adjustment-factors", action="store_true", help="Enrich the existing isolated cache with TuShare adjusted-price factors only.")
    parser.add_argument("--hydrate-adjustment-factors-by-date", action="store_true", help="Resumably enrich daily cross-sectional adjustment factors (recommended).")
    parser.add_argument("--limit", type=int, help="Optional instrument cap for resumable cache enrichment.")
    parser.add_argument("--signal-once", action="store_true", help="Generate one independent daily decision.")
    parser.add_argument("--paper-once", action="store_true", help="Run one local-only paper execution cycle.")
    parser.add_argument("--account", action="store_true", help="Print the isolated Q-GARP paper account.")
    parser.add_argument("--backtest", action="store_true", help="Run conservative point-in-time historical backtest from local cache.")
    parser.add_argument("--v3-diagnostics", action="store_true", help="Run v3 research-only factor IC and decile diagnostics; does not trade or notify.")
    parser.add_argument("--v3-backtest", action="store_true", help="Run v3's research-only point-in-time hysteresis backtest; does not trade or notify.")
    parser.add_argument("--v4-backtest", action="store_true", help="Run v4 regime-first research-only point-in-time backtest; does not trade or notify.")
    parser.add_argument("--v4-annual-backtest", action="store_true", help="Run independent v4 calendar-year backtests and report every year; research only.")
    parser.add_argument("--v4-optimize", action="store_true", help="Run bounded v4 annual research profiles; no paper trading or notifications.")
    parser.add_argument("--optimization-hours", type=float, default=3.0, help="Maximum wall-clock hours for --v4-optimize.")
    parser.add_argument("--v3-signal-once", action="store_true", help="Generate one v3 research-only plan; does not trade or notify.")
    parser.add_argument("--walk-forward-research", action="store_true", help="Run pre-registered Q-GARP profiles against calibration and untouched holdout periods; research only.")
    parser.add_argument(
        "--walk-forward-profiles",
        help="Comma-separated research profile names. Limits a later hypothesis test; never changes the production profile.",
    )
    parser.add_argument("--start", default="20170101")
    parser.add_argument("--end", default="20261231")
    args = parser.parse_args()
    runner = QGARPStrategyRunner()
    if args.refresh or args.backfill:
        print(json.dumps(runner.refresh(args.start if args.backfill else None, args.end if args.backfill else None), ensure_ascii=False))
        return 0
    if args.hydrate_adjustment_factors:
        print(json.dumps(runner.hydrate_adjustment_factors(args.start, args.end, args.limit), ensure_ascii=False))
        return 0
    if args.hydrate_adjustment_factors_by_date:
        print(json.dumps(runner.hydrate_adjustment_factors_by_date(args.start, args.end, args.limit or 20), ensure_ascii=False))
        return 0
    if args.signal_once:
        print(json.dumps(runner.signal_once(), ensure_ascii=False, default=str))
        return 0
    if args.paper_once:
        print(json.dumps(runner.paper_once(), ensure_ascii=False, default=str))
        return 0
    if args.account:
        print(json.dumps(runner.account(), ensure_ascii=False, default=str))
        return 0
    if args.backtest:
        print(json.dumps(run_historical_backtest(runner.store, runner.settings, args.start, args.end), ensure_ascii=False, default=str))
        return 0
    if args.v3_diagnostics:
        print(json.dumps(run_v3_factor_diagnostics(runner.store, args.start, args.end), ensure_ascii=False, default=str))
        return 0
    if args.v3_backtest:
        from qgarp_strategy.v3_backtest import run_v3_historical_backtest

        print(json.dumps(run_v3_historical_backtest(runner.store, runner.settings, args.start, args.end), ensure_ascii=False, default=str))
        return 0
    if args.v4_backtest:
        from qgarp_strategy.v3_backtest import run_v4_historical_backtest

        print(json.dumps(run_v4_historical_backtest(runner.store, runner.settings, args.start, args.end), ensure_ascii=False, default=str))
        return 0
    if args.v4_annual_backtest:
        from qgarp_strategy.v3_backtest import run_v4_annual_backtests

        print(json.dumps(run_v4_annual_backtests(runner.store, runner.settings, int(args.start[:4]), int(args.end[:4]), args.end), ensure_ascii=False, default=str))
        return 0
    if args.v4_optimize:
        from qgarp_strategy.v4_optimizer import run_v4_bounded_optimization

        print(json.dumps(run_v4_bounded_optimization(runner.settings, runner.store, args.optimization_hours), ensure_ascii=False, default=str))
        return 0
    if args.v3_signal_once:
        as_of = runner.store.latest_trade_date()
        result = build_v3_plan_from_store(runner.store, as_of) if as_of else {"status": "blocked", "reason": "Q-GARP 独立缓存为空"}
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    if args.walk_forward_research:
        from qgarp_strategy.robustness_research import run_walk_forward_research

        names = [name.strip() for name in (args.walk_forward_profiles or "").split(",") if name.strip()]
        print(json.dumps(run_walk_forward_research(names=names or None), ensure_ascii=False, default=str))
        return 0
    parser.print_help()
    return 0
