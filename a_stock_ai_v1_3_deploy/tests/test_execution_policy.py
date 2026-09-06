from __future__ import annotations

import json
import os
from contextlib import ExitStack
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.config import settings
from portfolio.account import (
    PaperAccountLoadError, PaperPosition, _commission, _max_affordable_quantity, account_summary,
    load_account, mark_to_market, new_account, save_account, simulate_buy, simulate_sell_all,
)
from portfolio.execution_policy import build_execution_plan
from scheduler.trading_calendar import BEIJING_TZ
from simulation.paper_trading import _latest_price_map, current_paper_account, run_paper_simulation_once


class ShanghaiClock(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = datetime(2026, 8, 19, 16, 30, tzinfo=timezone.utc)
        return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)


class PositionAwareExecutionPolicyTests(TestCase):
    def setUp(self) -> None:
        self.tuned = replace(
            settings, paper_initial_cash=100000.0, max_position_weight=0.12,
            paper_max_position_pct=0.12, paper_disciplined_execution_enabled=True,
            paper_policy_max_names=4, paper_policy_max_total_exposure=0.72,
            paper_policy_max_position_pct=0.12, paper_policy_min_rebalance_delta_pct=0.02,
            paper_policy_min_holding_days=3, paper_policy_hard_stop_loss_pct=0.065,
            paper_policy_trail_activation_pct=0.12, paper_policy_trailing_stop_pct=0.085,
            paper_policy_exit_panic_score=64.0, paper_slippage_pct=0.001,
            paper_commission_rate=0.00025, paper_min_commission=5.0, paper_lot_size=100,
        )
        for module in ("portfolio.account", "portfolio.execution_policy", "simulation.paper_trading"):
            self.enterContext(patch(f"{module}.settings", self.tuned))
        for module in ("portfolio.account", "portfolio.execution_policy"):
            self.enterContext(patch(f"{module}.datetime", ShanghaiClock))

    def recommendation(self, symbol="600000.SH", **overrides):
        return {
            "symbol": symbol, "name": "fixture", "current_price": 10.0,
            "position": 0.12, "rank_score": 90, "can_buy": True,
            "max_buy_price": 20.0, **overrides,
        }

    def held_account(self, **overrides):
        account = new_account()
        position = PaperPosition(**{
            "symbol": "600000.SH", "name": "fixture", "quantity": 1000,
            "available_quantity": 1000, "avg_cost": 10.0, "last_price": 10.0,
            "entry_date": "2026-08-10", "last_trade_date": "2026-08-19",
            "high_watermark": 10.0, **overrides,
        })
        account.positions[position.symbol] = position
        account.cash = account.initial_cash - position.quantity * position.last_price
        mark_to_market(account, {position.symbol: position.last_price})
        return account

    def run_cycle(self, account, signal, prices):
        with ExitStack() as stack:
            stack.enter_context(patch("simulation.paper_trading.load_account", return_value=account))
            stack.enter_context(patch("simulation.paper_trading._prepare_account_session", side_effect=lambda value: value))
            stack.enter_context(patch("simulation.paper_trading.paper_account_session_metrics", return_value={}))
            for name in ("save_account", "log_paper_order", "log_paper_trade", "log_paper_account_snapshot"):
                stack.enter_context(patch(f"simulation.paper_trading.{name}"))
            stack.enter_context(patch("realtime.market_stream.MarketStream"))
            return run_paper_simulation_once("fixture", signal, market_prices=prices)

    def test_generic_sell_does_not_liquidate_a_fresh_position(self) -> None:
        account = new_account()
        account.positions["600000.SH"] = PaperPosition(
            symbol="600000.SH",
            name="浦发银行",
            quantity=1000,
            available_quantity=1000,
            avg_cost=10.0,
            last_price=9.9,
            entry_date="2026-08-19",
            high_watermark=10.1,
        )
        signal = {
            "signal": "SELL",
            "state": {"state": "DOWNTREND"},
            "market_sentiment": {"trade_permission": "NO_BUY", "panic_score": 40},
        }

        plan = build_execution_plan(account, signal, {"600000.SH": 9.9}, as_of_date=date(2026, 8, 20))

        self.assertEqual(plan["sell_symbols"], [])

    def test_hard_stop_exits_even_before_minimum_holding_period(self) -> None:
        account = new_account()
        account.positions["600000.SH"] = PaperPosition(
            symbol="600000.SH",
            name="浦发银行",
            quantity=1000,
            available_quantity=1000,
            avg_cost=10.0,
            last_price=9.2,
            entry_date="2026-08-19",
            high_watermark=10.0,
        )

        plan = build_execution_plan(account, {"signal": "HOLD"}, {"600000.SH": 9.2}, as_of_date=date(2026, 8, 20))

        self.assertEqual(plan["sell_symbols"], ["600000.SH"])
        self.assertIn("硬止损", plan["sell_reasons"]["600000.SH"])

    def test_same_day_add_is_suppressed_and_rebalance_needs_material_delta(self) -> None:
        account = new_account()
        account.positions["600000.SH"] = PaperPosition(
            symbol="600000.SH",
            name="浦发银行",
            quantity=10_000,
            available_quantity=0,
            avg_cost=10.0,
            last_price=10.0,
            last_trade_date="2026-08-20",
            entry_date="2026-08-20",
            high_watermark=10.0,
        )
        signal = {
            "signal": "BUY",
            "recommendations": [{"symbol": "600000.SH", "name": "浦发银行", "current_price": 10.0, "position": 0.12, "rank_score": 90}],
        }

        plan = build_execution_plan(account, signal, {"600000.SH": 10.0}, as_of_date=date(2026, 8, 20))

        self.assertEqual(plan["buy_recommendations"], [])
        self.assertEqual(plan["buy_skips"][0]["reason"], "同一交易日不重复加仓")

    def test_exposure_budget_caps_new_positions(self) -> None:
        account = new_account()
        signal = {
            "signal": "BUY",
            "recommendations": [
                {"symbol": "600000.SH", "name": "浦发银行", "current_price": 10.0, "position": 0.12, "rank_score": 90},
                {"symbol": "600001.SH", "name": "邯郸钢铁", "current_price": 10.0, "position": 0.12, "rank_score": 89},
            ],
        }
        tuned = replace(self.tuned, paper_policy_max_total_exposure=0.15)
        with patch("portfolio.execution_policy.settings", tuned):
            plan = build_execution_plan(
                account,
                signal,
                {"600000.SH": 10.0, "600001.SH": 10.0},
                as_of_date=date(2026, 8, 20),
            )

        total_target = sum(item["position"] for item in plan["buy_recommendations"])
        self.assertLessEqual(total_target, 0.15)
        self.assertTrue(plan["buy_recommendations"])

    def test_exit_and_buy_are_mutually_exclusive_in_one_cycle(self) -> None:
        account = self.held_account(last_price=9.0)
        signal = {"signal": "BUY", "recommendations": [self.recommendation()]}
        plan = build_execution_plan(account, signal, {"600000.SH": 9.0})
        self.assertEqual(plan["sell_symbols"], ["600000.SH"])
        self.assertEqual(plan["buy_recommendations"], [])
        result = self.run_cycle(account, signal, {"600000.SH": 9.0})
        self.assertEqual([trade["side"] for trade in result["trades"]], ["SELL"])
        self.assertEqual(account.last_exit_dates, {"600000.SH": "2026-08-20"})
        self.assertEqual(self.run_cycle(account, signal, {"600000.SH": 9.0})["trades"], [])

    def test_cooldown_survives_full_exit_reload_and_expires_next_shanghai_day(self) -> None:
        account = self.held_account()
        simulate_sell_all(account, prices={"600000.SH": 9.0})
        self.assertFalse(account.positions)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            save_account(account, path)
            restored = load_account(path)
            self.assertEqual(restored.last_exit_dates, {"600000.SH": "2026-08-20"})
            signal = {"signal": "BUY", "recommendations": [self.recommendation()]}
            self.assertEqual(build_execution_plan(restored, signal, {"600000.SH": 10.0})["buy_recommendations"], [])
            order, trade = simulate_buy(restored, self.recommendation(), prices={"600000.SH": 10.0})
            self.assertIsNone(trade)
            self.assertEqual(order["skip_kind"], "reentry_cooldown")
            with patch("portfolio.account.datetime") as clock:
                clock.now.return_value = datetime(2026, 8, 21, 0, 1, tzinfo=BEIJING_TZ)
                restored = load_account(path)
                self.assertEqual(restored.last_exit_dates, {})
                plan = build_execution_plan(restored, signal, {"600000.SH": 10.0}, as_of_date=date(2026, 8, 21))
                self.assertTrue(plan["buy_recommendations"])
                _, trade = simulate_buy(restored, plan["buy_recommendations"][0], prices={"600000.SH": 10.0})
                self.assertIsNotNone(trade)

    def test_legacy_account_without_cooldown_metadata_loads_unchanged(self) -> None:
        account = self.held_account()
        payload = account.to_dict()
        payload.pop("last_exit_dates")
        payload["positions"]["600000.SH"].pop("entry_date")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            restored = load_account(path)
        self.assertEqual(restored.last_exit_dates, {})
        self.assertEqual(restored.cash, account.cash)
        self.assertEqual(restored.positions["600000.SH"].quantity, 1000)
        self.assertEqual(restored.positions["600000.SH"].entry_date, "2026-08-19")

    def test_nonfinite_persisted_cash_does_not_reset_account_or_inject_funds(self) -> None:
        for bad in (float("nan"), float("inf")):
            with self.subTest(bad=bad), TemporaryDirectory() as directory:
                account = self.held_account()
                payload = account.to_dict()
                payload["cash"] = bad
                path = Path(directory) / "account.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                before = path.read_bytes()
                with self.assertRaisesRegex(PaperAccountLoadError, "cash must be a finite number"):
                    load_account(path)
                self.assertEqual(path.read_bytes(), before)

    def test_new_cooldown_default_serializes_as_independent_empty_object(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            account = load_account(path)
            self.assertFalse(path.exists())
            self.assertEqual(account.cash, self.tuned.paper_initial_cash)
            self.assertEqual(account.last_exit_dates, {})
            other = new_account()
            self.assertIsNot(account.last_exit_dates, other.last_exit_dates)
            save_account(account, path)
            self.assertEqual(json.loads(path.read_text())["last_exit_dates"], {})
            snapshot = account.to_dict()
            snapshot["last_exit_dates"]["600000.SH"] = "2026-08-20"
            self.assertEqual(account.last_exit_dates, {})

    def test_legacy_positional_position_preserves_strategy_id(self) -> None:
        position = PaperPosition("600000.SH", "fixture", 1000, 0, 10.0, 10.0, 10000.0, 0.0,
                                 "2026-08-19", "hybrid_alpha")
        self.assertEqual(position.strategy_id, "hybrid_alpha")
        self.assertEqual(position.entry_date, "")
        self.assertEqual(position.high_watermark, 0.0)

    def test_atomic_save_replaces_only_after_complete_json_and_preserves_mode(self) -> None:
        account = self.held_account()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            save_account(account, path)
            path.chmod(0o640)
            original_bytes = path.read_bytes()
            account.last_exit_dates["600001.SH"] = "2026-08-20"
            real_replace = os.replace

            def inspect_replace(source, destination):
                self.assertNotEqual(Path(source), path)
                self.assertEqual(Path(source).parent, path.parent)
                self.assertEqual(Path(destination), path)
                self.assertEqual(path.read_bytes(), original_bytes)
                pending = json.loads(Path(source).read_text())
                self.assertEqual(pending["last_exit_dates"], account.last_exit_dates)
                self.assertEqual(pending["cash"], account.cash)
                real_replace(source, destination)

            with patch("portfolio.account.os.replace", side_effect=inspect_replace) as replace_file, patch(
                "portfolio.account.os.fsync", wraps=os.fsync
            ) as fsync:
                save_account(account, path)
            replace_file.assert_called_once()
            fsync.assert_called_once()
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(load_account(path).last_exit_dates, account.last_exit_dates)
            self.assertEqual(list(path.parent.glob(".account.json.*.tmp")), [])

    def test_failed_atomic_save_preserves_previous_file_and_timestamp(self) -> None:
        account = self.held_account()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            save_account(account, path)
            previous = path.read_bytes()
            previous_timestamp = account.updated_at
            account.cash -= 100.0
            with patch("portfolio.account._now", return_value="2026-08-20T01:00:00+08:00"), patch(
                "portfolio.account.os.replace", side_effect=OSError("replace failed")
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    save_account(account, path)
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(account.updated_at, previous_timestamp)
            self.assertEqual(list(path.parent.glob(".account.json.*.tmp")), [])
            with patch("portfolio.account.os.fsync", side_effect=OSError("disk failed")):
                with self.assertRaisesRegex(OSError, "disk failed"):
                    save_account(account, path)
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(path.parent.glob(".account.json.*.tmp")), [])

    def test_invalid_serialization_never_overwrites_existing_account(self) -> None:
        account = self.held_account()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            save_account(account, path)
            previous = path.read_bytes()
            account.cash = float("nan")
            with patch("portfolio.account.os.replace") as replace_file:
                with self.assertRaises(ValueError):
                    save_account(account, path)
            replace_file.assert_not_called()
            self.assertEqual(path.read_bytes(), previous)

    def test_corrupt_or_incomplete_existing_file_never_initializes_or_overwrites(self) -> None:
        incomplete = self.held_account().to_dict()
        del incomplete["cash"]
        for contents in ("", "{", "[]", "{}", json.dumps(incomplete)):
            with self.subTest(contents=contents), TemporaryDirectory() as directory:
                path = Path(directory) / "account.json"
                path.write_text(contents, encoding="utf-8")
                with patch("portfolio.account.new_account") as initialize:
                    with self.assertRaisesRegex(PaperAccountLoadError, "Refusing to initialize") as failure:
                        load_account(path)
                initialize.assert_not_called()
                self.assertIn(str(path), str(failure.exception))
                self.assertEqual(path.read_text(), contents)

    def test_dashboard_and_worker_propagate_corrupt_account_failure_without_save(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            path.write_text("{broken", encoding="utf-8")
            with patch("portfolio.account.settings", replace(self.tuned, paper_account_path=path)), patch(
                "simulation.paper_trading.save_account"
            ) as save, patch("simulation.paper_trading._prepare_account_session") as prepare, patch(
                "simulation.paper_trading.paper_account_session_metrics"
            ) as metrics:
                with self.assertRaises(PaperAccountLoadError):
                    current_paper_account()
                with self.assertRaises(PaperAccountLoadError):
                    run_paper_simulation_once("fixture", {"signal": "HOLD"}, market_prices={})
            save.assert_not_called()
            prepare.assert_not_called()
            metrics.assert_not_called()
            self.assertEqual(path.read_text(), "{broken")

    def test_next_day_t1_rollover_and_fresh_mark_precede_execution_policy(self) -> None:
        account = self.held_account(available_quantity=0, last_trade_date="2026-08-19")
        events = []
        with TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            save_account(account, path)
            self.assertEqual(json.loads(path.read_text())["positions"]["600000.SH"]["available_quantity"], 0)

            def load():
                restored = load_account(path)
                self.assertEqual(restored.positions["600000.SH"].available_quantity, 1000)
                events.append("load_and_rollover")
                return restored

            def mark(restored, prices):
                events.append("mark")
                return mark_to_market(restored, prices)

            def plan(restored, signal, prices):
                events.append("policy")
                position = restored.positions["600000.SH"]
                self.assertEqual(position.available_quantity, 1000)
                self.assertEqual(position.last_price, 9.0)
                self.assertEqual(position.market_value, 9000.0)
                return build_execution_plan(restored, signal, prices)

            with ExitStack() as stack:
                stack.enter_context(patch("simulation.paper_trading.load_account", side_effect=load))
                stack.enter_context(patch("simulation.paper_trading.mark_to_market", side_effect=mark))
                stack.enter_context(patch("simulation.paper_trading.build_execution_plan", side_effect=plan))
                stack.enter_context(patch("simulation.paper_trading.save_account", side_effect=lambda value: save_account(value, path)))
                stack.enter_context(patch("simulation.paper_trading._prepare_account_session", side_effect=lambda value: value))
                stack.enter_context(patch("simulation.paper_trading.paper_account_session_metrics", return_value={}))
                for name in ("log_paper_order", "log_paper_trade", "log_paper_account_snapshot"):
                    stack.enter_context(patch(f"simulation.paper_trading.{name}"))
                stack.enter_context(patch("realtime.market_stream.MarketStream"))
                result = run_paper_simulation_once("fixture", {"signal": "HOLD"}, market_prices={"600000.SH": 9.0})
            self.assertEqual(events[:3], ["load_and_rollover", "mark", "policy"])
            self.assertEqual([(trade["side"], trade["quantity"]) for trade in result["trades"]], [("SELL", 1000)])
            self.assertEqual(load_account(path).last_exit_dates, {"600000.SH": "2026-08-20"})

    def test_partial_exit_also_blocks_adds_and_preserves_t1_lot(self) -> None:
        account = self.held_account(available_quantity=500, last_trade_date="2026-08-20")
        _, trades = simulate_sell_all(account, prices={"600000.SH": 9.0})
        self.assertEqual(trades[0]["quantity"], 500)
        self.assertEqual(account.positions["600000.SH"].quantity, 500)
        self.assertEqual(account.positions["600000.SH"].available_quantity, 0)
        order, trade = simulate_buy(account, self.recommendation())
        self.assertIsNone(trade)
        self.assertEqual(order["skip_kind"], "reentry_cooldown")

    def test_t1_locked_exit_does_not_start_cooldown(self) -> None:
        account = self.held_account(available_quantity=0, last_trade_date="2026-08-20")
        orders, trades = simulate_sell_all(account, prices={"600000.SH": 9.0})
        self.assertFalse(trades)
        self.assertEqual(orders[0]["skip_kind"], "t1_locked")
        self.assertEqual(account.last_exit_dates, {})

    def test_fresh_price_controls_cap_quantity_fill_and_marks(self) -> None:
        signal = {"signal": "BUY", "recommendations": [self.recommendation(max_buy_price=10.5)]}
        account = new_account()
        blocked = self.run_cycle(account, signal, {"600000.SH": 11.0})
        self.assertFalse(blocked["trades"])
        self.assertEqual(account.cash, account.initial_cash)
        signal["recommendations"][0]["max_buy_price"] = 20.0
        filled = self.run_cycle(account, signal, {"600000.SH": 11.0})
        self.assertEqual(len(filled["trades"]), 1)
        self.assertEqual(filled["trades"][0]["price"], 11.011)
        self.assertEqual(filled["trades"][0]["quantity"], 1000)
        self.assertEqual(account.positions["600000.SH"].last_price, 11.0)
        self.assertEqual(account.positions["600000.SH"].high_watermark, 11.0)
        self.assertLess(account_summary(account)["equity"], account.initial_cash)
        self.assertEqual(signal["recommendations"][0]["current_price"], 10.0)

    def test_account_fill_independently_rechecks_fresh_price_cap(self) -> None:
        account = new_account()
        order, trade = simulate_buy(account, self.recommendation(max_buy_price=10.5), prices={"600000.SH": 11.0})
        self.assertEqual(order["status"], "rejected")
        self.assertIsNone(trade)
        self.assertEqual(account.cash, account.initial_cash)

    def test_missing_fresh_held_quote_never_executes_stale_exit(self) -> None:
        account = self.held_account(last_price=9.0)
        signal = {
            "signal": "SELL", "state": {"state": "DOWNTREND"},
            "market_sentiment": {"trade_permission": "NO_BUY", "panic_score": 100},
            "watchlist": [self.recommendation(current_price=9.0)],
        }
        before = account.to_dict()
        result = self.run_cycle(account, signal, {})
        self.assertFalse(result["trades"])
        self.assertEqual(account.to_dict(), before)
        orders, trades = simulate_sell_all(account, prices={})
        self.assertFalse(trades)
        self.assertEqual(orders[0]["skip_kind"], "missing_price")

    def test_quote_map_never_seeds_from_stale_recommendations(self) -> None:
        recs = [self.recommendation()]
        with patch("realtime.market_stream.MarketStream") as stream:
            stream.return_value.quotes_for_symbols.return_value = []
            self.assertEqual(_latest_price_map(recs, recs, {}, ["600000.SH"]), {})
            stream.return_value.quotes_for_symbols.assert_called_once_with(["600000.SH"])
            stream.return_value.quotes_for_symbols.return_value = [SimpleNamespace(symbol="600000.SH", price=11.0)]
            self.assertEqual(_latest_price_map(recs, [], None, ["600000.SH"]), {"600000.SH": 11.0})
            stream.return_value.quotes_for_symbols.side_effect = RuntimeError("offline")
            self.assertEqual(_latest_price_map(recs, [], {}, ["600000.SH"]), {})

    def test_nonfinite_and_nonpositive_prices_weights_and_caps_fail_closed(self) -> None:
        for bad in (0, -1, float("nan"), float("inf"), -float("inf"), None, "bad"):
            for field in ("price", "position", "max_buy_price"):
                with self.subTest(field=field, bad=bad):
                    account = new_account()
                    rec = self.recommendation(target_weight=0.12)
                    prices = {"600000.SH": 10.0}
                    if field == "price":
                        prices["600000.SH"] = bad
                    else:
                        rec[field] = bad
                    plan = build_execution_plan(account, {"signal": "BUY", "recommendations": [rec]}, prices)
                    self.assertFalse(plan["buy_recommendations"])
                    _, trade = simulate_buy(account, rec, prices=prices)
                    self.assertIsNone(trade)
                    self.assertEqual(account.cash, account.initial_cash)

    def test_invalid_cash_and_missing_weight_do_not_create_orders(self) -> None:
        for cash in (0, -1, float("nan"), float("inf")):
            with self.subTest(cash=cash):
                account = new_account()
                account.cash = cash
                rec = self.recommendation()
                self.assertFalse(build_execution_plan(account, {"signal": "BUY", "recommendations": [rec]}, {"600000.SH": 10.0})["buy_recommendations"])
                self.assertIsNone(simulate_buy(account, rec)[1])
        rec = self.recommendation()
        del rec["position"]
        self.assertIsNone(simulate_buy(new_account(), rec)[1])

    def test_invalid_fresh_prices_do_not_poison_marks_or_fill_exits(self) -> None:
        for bad in (0, -1, float("nan"), float("inf"), None):
            with self.subTest(bad=bad):
                account = self.held_account(last_price=9.0)
                mark_to_market(account, {"600000.SH": bad})
                self.assertEqual(account.positions["600000.SH"].last_price, 9.0)
                self.assertFalse(simulate_sell_all(account, prices={"600000.SH": bad})[1])
                self.assertFalse(account.last_exit_dates)

    def test_sell_price_rounded_to_zero_does_not_fill_or_set_cooldown(self) -> None:
        account = self.held_account()
        orders, trades = simulate_sell_all(account, prices={"600000.SH": 0.000001})
        self.assertFalse(trades)
        self.assertEqual(orders[0]["skip_kind"], "invalid_price")
        self.assertFalse(account.last_exit_dates)

    def test_duplicates_and_rejected_candidates_do_not_reserve_slots_or_cash(self) -> None:
        recs = [
            self.recommendation("600002.SH", can_buy=False, rank_score=100),
            self.recommendation("600003.SH", max_buy_price=9.0, rank_score=99),
            self.recommendation("600004.SH", current_price=1000.0, max_buy_price=2000.0, position=0.03, rank_score=98),
            self.recommendation(), self.recommendation(rank_score=89), self.recommendation("600001.SH"),
        ]
        prices = {item["symbol"]: item["current_price"] for item in recs}
        tuned = replace(self.tuned, paper_policy_max_names=2)
        account = new_account()
        with patch("portfolio.execution_policy.settings", tuned):
            plan = build_execution_plan(account, {"signal": "BUY", "recommendations": recs}, prices)
        self.assertEqual([item["symbol"] for item in plan["buy_recommendations"]], ["600000.SH", "600001.SH"])
        self.assertEqual(len(plan["buy_skips"]), 4)
        self.assertEqual(account.positions, {})

    def test_cash_reservations_include_commission_and_bound_actual_fills(self) -> None:
        account = self.held_account(symbol="600009.SH", quantity=9550, available_quantity=9550)
        recs = [self.recommendation(f"{600000 + index}.SH", position=0.02) for index in range(3)]
        prices = {item["symbol"]: 10.0 for item in recs}
        # Isolate cash/fee reservation from the independent portfolio exposure cap.
        with patch("portfolio.execution_policy.settings", replace(self.tuned, paper_policy_max_total_exposure=1.0)):
            plan = build_execution_plan(account, {"signal": "BUY", "recommendations": recs}, prices)
        self.assertEqual(len(plan["buy_recommendations"]), 2)
        reserved = sum(item["execution_reserved_cash"] for item in plan["buy_recommendations"])
        self.assertLessEqual(reserved, account.cash)
        initial_cash = account.cash
        for item in plan["buy_recommendations"]:
            _, trade = simulate_buy(account, item, prices=prices)
            self.assertIsNotNone(trade)
            self.assertLessEqual(trade["quantity"], item["execution_quantity_cap"])
            self.assertLessEqual(trade["amount"] + trade["fee"], item["execution_reserved_cash"])
        self.assertGreaterEqual(account.cash, 0)
        self.assertLessEqual(initial_cash - account.cash, reserved + 0.0001)

    def test_changed_fill_quote_cannot_exceed_planned_cash_reservation(self) -> None:
        account = new_account()
        plan = build_execution_plan(account, {"signal": "BUY", "recommendations": [self.recommendation()]}, {"600000.SH": 10.0})
        item = plan["buy_recommendations"][0]
        _, trade = simulate_buy(account, item, prices={"600000.SH": 11.0})
        self.assertIsNotNone(trade)
        self.assertLessEqual(trade["amount"] + trade["fee"], item["execution_reserved_cash"])

    def test_account_also_blocks_duplicate_same_day_adds(self) -> None:
        account = new_account()
        self.assertIsNotNone(simulate_buy(account, self.recommendation(position=0.05))[1])
        order, trade = simulate_buy(account, self.recommendation(position=0.12))
        self.assertIsNone(trade)
        self.assertEqual(order["skip_kind"], "same_day_add")

    def test_exact_single_lot_cash_reservation_survives_float_round_trip(self) -> None:
        account = new_account()
        account.cash = 12999.0
        rec = self.recommendation(current_price=13.3304)
        prices = {"600000.SH": 13.3304}
        plan = build_execution_plan(account, {"signal": "BUY", "recommendations": [rec]}, prices)
        item = plan["buy_recommendations"][0]
        self.assertEqual(item["execution_quantity_cap"], 100)
        self.assertEqual(item["execution_reserved_cash"], 1339.37)
        _, trade = simulate_buy(account, item, prices=prices)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["quantity"], 100)

    def test_commission_aware_affordability_is_maximal_and_nonnegative(self) -> None:
        for cash in (1000.0, 100010.0, 1001000.0):
            for rate in (0.00025, 0.01):
                with self.subTest(cash=cash, rate=rate), patch("portfolio.account.settings", replace(self.tuned, paper_commission_rate=rate)):
                    quantity = _max_affordable_quantity(cash, 10.0)
                    self.assertLessEqual(quantity * 10.0 + _commission(quantity * 10.0), cash)
                    next_amount = (quantity + 100) * 10.0
                    self.assertGreater(next_amount + _commission(next_amount), cash)

    def test_legacy_execution_keeps_correctness_guards(self) -> None:
        account = new_account()
        account.last_exit_dates["600000.SH"] = "2026-08-20"
        signal = {"signal": "BUY", "recommendations": [self.recommendation(), self.recommendation("600001.SH"), self.recommendation("600001.SH")]}
        with patch("portfolio.execution_policy.settings", replace(self.tuned, paper_disciplined_execution_enabled=False)):
            plan = build_execution_plan(account, signal, {"600000.SH": 10.0, "600001.SH": 11.0})
        self.assertFalse(plan["enabled"])
        self.assertEqual([item["symbol"] for item in plan["buy_recommendations"]], ["600001.SH"])
        self.assertEqual(plan["buy_recommendations"][0]["current_price"], 11.0)

    def test_risk_settings_and_existing_exposure_are_not_reinterpreted_as_bundle_cap(self) -> None:
        account = self.held_account(symbol="600009.SH", quantity=2000, available_quantity=2000)
        signal = {"signal": "BUY", "position": 0.12, "recommendations": [self.recommendation()]}
        plan = build_execution_plan(account, signal, {"600000.SH": 10.0, "600009.SH": 10.0})
        self.assertTrue(plan["buy_recommendations"])
        self.assertEqual(plan["sell_symbols"], [])

    def test_existing_hard_stop_and_trailing_thresholds_are_unchanged(self) -> None:
        for price, peak, should_exit in ((9.36, 10.0, False), (9.34, 10.0, True),
                                        (10.99, 12.0, False), (10.97, 12.0, True)):
            with self.subTest(price=price, peak=peak):
                account = self.held_account(last_price=price, high_watermark=peak)
                plan = build_execution_plan(account, {"signal": "HOLD"}, {"600000.SH": price})
                self.assertEqual(bool(plan["sell_symbols"]), should_exit)
