from __future__ import annotations

import argparse
import json
import logging
import sys

from app.config import settings
from storage.logger import healthcheck, init_db


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="A-share realtime market perception engine v1.9")
    parser.add_argument("--once", action="store_true", help="Run one intraday decision cycle and exit.")
    parser.add_argument("--review-once", action="store_true", help="Run one daily review article cycle and exit.")
    parser.add_argument("--learn-once", action="store_true", help="Run one silent self-learning parameter cycle and exit.")
    parser.add_argument("--simulate-once", action="store_true", help="Replay the latest signal into the paper account.")
    parser.add_argument("--etf-minute-once", action="store_true", help="Run one ETF minute paper cycle without sending a push.")
    parser.add_argument("--etf-storage-report", action="store_true", help="Print a read-only ETF minute-storage report.")
    parser.add_argument("--paper-account", action="store_true", help="Print the current paper account state.")
    parser.add_argument("--paper-reset", action="store_true", help="Reset the local paper account to initial cash.")
    parser.add_argument("--paper-reconcile-session", action="store_true", help="Attach stock paper logs to the active reset session.")
    parser.add_argument("--backtest", action="store_true", help="Run a signal replay backtest from logged BUY decisions.")
    parser.add_argument("--backtest-multi", action="store_true", help="Replay logged v1.9 multi-strategy portfolio signals.")
    parser.add_argument("--historical-backtest", action="store_true", help="Run a daily historical market-data backtest.")
    parser.add_argument("--optimize-historical", action="store_true", help="Run a historical strategy parameter search.")
    parser.add_argument("--backtest-days", type=int, default=None, help="Backtest lookback window in calendar days.")
    parser.add_argument("--historical-days", type=int, default=180, help="Historical daily backtest lookback window.")
    parser.add_argument("--historical-start", default=None, help="Historical daily backtest start date, YYYYMMDD.")
    parser.add_argument("--historical-end", default=None, help="Historical daily backtest end date, YYYYMMDD.")
    parser.add_argument(
        "--historical-universe-profile",
        default="watchlist",
        choices=["watchlist", "default", "large", "active_mid", "blended", "institutional", "adaptive"],
        help="Historical backtest universe construction profile.",
    )
    parser.add_argument("--historical-universe-limit", type=int, default=300, help="Historical universe max symbols.")
    parser.add_argument("--historical-universe-asof", default=None, help="Historical universe as-of date, YYYYMMDD.")
    parser.add_argument("--holding-days", type=int, default=None, help="Backtest holding window in trading bars.")
    parser.add_argument(
        "--strategy-profile",
        default="conservative",
        choices=[
            "conservative",
            "aggressive",
            "breakout",
            "optimized",
            "runner",
            "quality_t",
            "regime_adaptive",
            "regime_adaptive_balanced_trend",
            "hybrid_alpha",
        ],
        help="Historical backtest strategy profile.",
    )
    parser.add_argument("--healthcheck", action="store_true", help="Check latest SQLite cycle status.")
    args = parser.parse_args()

    configure_logging()
    init_db()

    if args.healthcheck:
        ok, message = healthcheck()
        print(message)
        return 0 if ok else 1

    if args.once:
        from realtime.intraday_loop import IntradayLoop

        cycle_id = IntradayLoop().run_once(trigger_source="manual")
        print(f"cycle_id={cycle_id}")
        return 0

    if args.review_once:
        if not settings.review_enabled:
            print("review_disabled")
            return 0
        from review.daily_review import run_daily_review

        review_id = run_daily_review(trigger_source="manual")
        print(f"review_id={review_id}")
        return 0

    if args.learn_once:
        if not settings.self_learning_enabled:
            print("self_learning_disabled")
            return 0
        from learning.self_learning import run_self_learning

        result = run_self_learning(trigger_source="manual")
        print(
            f"learning_id={result.get('run_id')} status={result.get('status')} "
            f"samples={result.get('sample_count', 0)} evaluated={result.get('evaluated_count', 0)}"
        )
        return 0

    if args.paper_reset:
        from simulation.paper_trading import reset_paper_account

        result = reset_paper_account()
        summary = result.get("summary", {})
        print(
            f"paper_account_reset snapshot_id={result.get('snapshot_id')} "
            f"cash={summary.get('cash')} equity={summary.get('equity')}"
        )
        return 0

    if args.paper_reconcile_session:
        from simulation.paper_trading import reconcile_current_paper_account_session

        account = reconcile_current_paper_account_session()
        metrics = account.get("session_metrics") or {}
        print(
            f"paper_account_session account_id={account.get('account_id')} "
            f"started_at={account.get('session_started_at')} "
            f"filled={metrics.get('filled_order_count', 0)} sells={metrics.get('sell_trade_count', 0)}"
        )
        return 0

    if args.paper_account:
        from simulation.paper_trading import current_paper_account

        account = current_paper_account()
        print(
            f"paper_account equity={account.get('equity')} cash={account.get('cash')} "
            f"market_value={account.get('market_value')} return_pct={account.get('return_pct')} "
            f"positions={len(account.get('positions') or {})}"
        )
        return 0

    if args.simulate_once:
        from simulation.paper_trading import run_paper_simulation_once

        result = run_paper_simulation_once()
        summary = result.get("summary", {})
        print(
            f"paper_simulation status={result.get('status')} snapshot_id={result.get('snapshot_id')} "
            f"signal={summary.get('signal')} filled={summary.get('filled_orders', 0)} "
            f"equity={summary.get('equity')} return_pct={summary.get('return_pct')}"
        )
        return 0

    if args.etf_minute_once:
        from etf_strategy.live_runner import ETFMinutePaperRunner

        result = ETFMinutePaperRunner().run_once()
        print(
            f"etf_minute status={result.get('status')} filled={len(result.get('orders') or [])} "
            f"reason={result.get('reason') or '-'}"
        )
        return 0

    if args.etf_storage_report:
        from etf_strategy.config import load_settings as load_etf_settings
        from etf_strategy.maintenance import minute_storage_report

        print(json.dumps(minute_storage_report(load_etf_settings().db_path), ensure_ascii=False, indent=2))
        return 0

    if args.backtest or args.backtest_multi:
        from backtest.signal_replay import run_signal_replay_backtest

        result = run_signal_replay_backtest(
            lookback_days=args.backtest_days,
            holding_days=args.holding_days,
            multi_strategy=args.backtest_multi,
        )
        metrics = result.get("metrics", {})
        print(
            f"backtest source={'multi_strategy' if args.backtest_multi else 'legacy'} run_id={result.get('run_id')} status={result.get('status')} "
            f"trades={metrics.get('trade_count', 0)} win_rate={metrics.get('win_rate', 0)} "
            f"avg_return_pct={metrics.get('avg_return_pct', 0)} "
            f"total_return_pct={metrics.get('total_return_pct', 0)} "
            f"max_drawdown_pct={metrics.get('max_drawdown_pct', 0)}"
        )
        return 0

    if args.historical_backtest:
        from backtest.historical_daily import run_historical_daily_backtest

        result = run_historical_daily_backtest(
            lookback_days=args.historical_days,
            holding_days=args.holding_days,
            profile_name=args.strategy_profile,
            start_date=args.historical_start,
            end_date=args.historical_end,
            universe_profile=args.historical_universe_profile,
            universe_limit=args.historical_universe_limit,
            universe_asof=args.historical_universe_asof,
        )
        metrics = result.get("metrics", {})
        print(
            f"historical_backtest run_id={result.get('run_id')} status={result.get('status')} "
            f"period={result.get('start_date')}-{result.get('end_date')} "
            f"trades={metrics.get('trade_count', 0)} win_rate={metrics.get('win_rate', 0)} "
            f"return_pct={metrics.get('total_return_pct', 0)} "
            f"max_drawdown_pct={metrics.get('max_drawdown_pct', 0)} "
            f"chart={result.get('chart_path')}"
        )
        return 0

    if args.optimize_historical:
        from backtest.historical_daily import run_historical_parameter_search

        result = run_historical_parameter_search(
            lookback_days=args.historical_days,
            profile_name=args.strategy_profile,
        )
        top = result.get("top") or []
        best = top[0] if top else {}
        print(
            f"historical_optimization run_id={result.get('run_id')} status={result.get('status')} "
            f"searched={result.get('searched_count')} candidates={result.get('candidate_count')} "
            f"best_return_pct={best.get('return_pct')} best_drawdown_pct={best.get('max_drawdown_pct')} "
            f"best_score={best.get('score')} result={result.get('result_path')}"
        )
        for index, item in enumerate(top[:5], start=1):
            print(
                f"top{index} return={item.get('return_pct')} dd={item.get('max_drawdown_pct')} "
                f"trades={item.get('trade_count')} win={item.get('win_rate')} score={item.get('score')} "
                f"profile={item.get('profile')}"
            )
        return 0

    from scheduler.job import start_scheduler

    start_scheduler()
    return 0


if __name__ == "__main__":
    sys.exit(main())
