from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from backtest import historical_daily as daily


DATES = ("20240102", "20240103", "20240104", "20240105", "20240108", "20240109")


def bar(date: str, price: float = 10.0, **overrides) -> dict:
    return {"trade_date": date, "open": price, "high": price, "low": price,
            "close": price, "pct_chg": 0.0, "amount": 1000000.0, **overrides}


def candidate(symbol: str, weight: float = 0.25, **overrides) -> dict:
    return {"symbol": symbol, "rank_score": 90.0, "sentiment_score": 80.0,
            "entry_weight": weight, **overrides}


def position(**overrides) -> daily.DailyPosition:
    return daily.DailyPosition(**{
        "symbol": "A", "entry_date": DATES[0], "entry_price": 10.0,
        "quantity": 1000, "stop_loss": 9.0, "take_profit": 15.0,
        "trailing_stop_pct": 0.0, "trail_activation_pct": 0.1,
        "highest_price": 10.0, "max_holding_days": 100, **overrides,
    })


class HistoricalExecutionTimingTests(TestCase):
    def setUp(self) -> None:
        tuned = replace(daily.settings, paper_initial_cash=10000.0,
                        paper_slippage_pct=0.0, paper_commission_rate=0.0,
                        paper_min_commission=0.0, paper_stamp_duty_rate=0.0,
                        paper_lot_size=100)
        self.enterContext(patch.object(daily, "settings", tuned))
        self.profile = daily._strategy_profile("conservative", overrides={
            "max_total_exposure": 1.0, "max_names": 3, "per_trade_weight": 0.25,
            "holding_days": 100, "stop_loss_pct": 0.1, "take_profit_pct": 0.5,
            "trailing_stop_pct": 0.0, "runner_enabled": False,
            "market_exit_enabled": False, "quality_t_enabled": False,
            "stop_loss_cooldown_enabled": False, "drawdown_cooldown_enabled": False,
        })
        self.addCleanup(daily._REGIME_CACHE.clear)

    def bars(self, *symbols: str) -> dict:
        return {symbol: [bar(date, 11.0 if date == DATES[-1] else 10.0) for date in DATES]
                for symbol in symbols}

    def run_backtest(self, bars: dict, signals: dict, **overrides) -> dict:
        daily._REGIME_CACHE.clear()
        with patch.object(daily, "_select_candidates", side_effect=lambda date, *_: signals.get(date, [])):
            return daily._run_portfolio_backtest(
                deepcopy(bars), DATES[0], DATES[-1], {**self.profile, **overrides},
            )

    def buys(self, result: dict, date: str) -> list[dict]:
        return [trade for trade in result["trades"]
                if trade["side"] == "BUY" and trade["trade_date"] == date]

    def test_post_open_close_cannot_change_open_purchase_size(self) -> None:
        bars = self.bars("A", "B")
        signals = {DATES[0]: [candidate("A", 0.5)], DATES[1]: [candidate("B")]}
        baseline = self.run_backtest(bars, signals)
        bars["A"][2].update(high=14.0, close=14.0)
        changed = self.run_backtest(bars, signals)
        self.assertEqual(self.buys(baseline, DATES[2]), self.buys(changed, DATES[2]))
        self.assertEqual(self.buys(changed, DATES[2])[0]["quantity"], 200)

    def test_future_intraday_sale_cannot_finance_open_purchase(self) -> None:
        bars = self.bars("A", "B")
        signals = {DATES[0]: [candidate("A", 0.9)], DATES[1]: [candidate("B", 0.5)]}
        baseline = self.run_backtest(bars, signals)
        bars["A"][2].update(low=5.0)
        changed = self.run_backtest(bars, signals)
        self.assertEqual(self.buys(baseline, DATES[2]), self.buys(changed, DATES[2]))
        self.assertEqual(self.buys(changed, DATES[2])[0]["quantity"], 100)
        day_trades = [trade for trade in changed["trades"] if trade["trade_date"] == DATES[2]]
        self.assertEqual([trade["side"] for trade in day_trades], ["BUY", "SELL"])

    def test_intraday_exit_does_not_free_an_opening_slot(self) -> None:
        bars = self.bars("A", "B")
        bars["A"][2].update(low=5.0)
        result = self.run_backtest(bars, {
            DATES[0]: [candidate("A")], DATES[1]: [candidate("B")],
            DATES[2]: [candidate("B")],
        }, max_names=1)
        self.assertEqual(self.buys(result, DATES[2]), [])
        self.assertEqual(self.buys(result, DATES[3])[0]["symbol"], "B")

    def test_max_names_limits_concurrent_holdings_across_days(self) -> None:
        result = self.run_backtest(self.bars("A", "B", "C"), {
            DATES[0]: [candidate("A", 0.1)], DATES[1]: [candidate("B", 0.1)],
            DATES[2]: [candidate("C", 0.1)],
        }, max_names=2)
        self.assertEqual([trade["symbol"] for trade in result["trades"] if trade["side"] == "BUY"], ["A", "B"])

    def test_gap_stop_fills_at_open_not_above_day_high(self) -> None:
        bars = self.bars("A")
        bars["A"][2] = bar(DATES[2], 5.0, low=4.0, high=6.0)
        result = self.run_backtest(bars, {DATES[0]: [candidate("A")]})
        sale = next(trade for trade in result["trades"] if trade["side"] == "SELL")
        self.assertEqual(sale["exit_reason"], "stop_loss")
        self.assertEqual(sale["price"], 5.0)
        self.assertLessEqual(sale["price"], bars["A"][2]["high"])

    def test_missing_bars_carry_last_mark_and_remain_unliquidated(self) -> None:
        bars = self.bars("A", "CALENDAR")
        bars["A"] = [bar(DATES[0]), bar(DATES[1], high=12.0, close=12.0)]
        result = self.run_backtest(bars, {DATES[0]: [candidate("A", 0.5)]})
        self.assertEqual([point["equity"] for point in result["curve"][1:]], [11000.0] * 5)
        self.assertEqual([trade["side"] for trade in result["trades"]], ["BUY"])
        self.assertEqual(result["open_positions"][0]["quantity"], 500)
        self.assertEqual(result["open_positions"][0]["mark_price"], 12.0)
        self.assertEqual(result["metrics"]["final_equity"], 11000.0)

    def test_missing_position_still_consumes_open_exposure_and_name_slot(self) -> None:
        bars = self.bars("A", "B")
        bars["A"] = bars["A"][:2]
        signals = {DATES[0]: [candidate("A", 0.5)], DATES[1]: [candidate("B", 0.5)]}
        capped = self.run_backtest(bars, signals, max_total_exposure=0.5)
        slotted = self.run_backtest(bars, signals, max_names=1)
        self.assertEqual(self.buys(capped, DATES[2]), [])
        self.assertEqual(self.buys(slotted, DATES[2]), [])

    def test_invalid_open_never_falls_back_to_future_close(self) -> None:
        for invalid in (0.0, None, -1.0, float("nan"), float("inf"), "bad"):
            with self.subTest(invalid=invalid):
                bars = self.bars("A")
                bars["A"][1]["open"] = invalid
                result = self.run_backtest(bars, {DATES[0]: [candidate("A")]})
                self.assertEqual(result["trades"], [])

    def test_invalid_final_close_never_creates_a_fill(self) -> None:
        for invalid in (0.0, None, -1.0, float("nan"), float("inf"), "bad"):
            with self.subTest(invalid=invalid):
                holding = position()
                bars = {DATES[1]: {"A": bar(DATES[1], close=invalid)}}
                cash, remaining, trades = daily._force_close_positions(100.0, [holding], bars, DATES[1])
                self.assertEqual(cash, 100.0)
                self.assertEqual(remaining, [holding])
                self.assertEqual(trades, [])

    def test_missing_marks_skip_invalid_history_and_never_use_future_bars(self) -> None:
        holding = position()
        bars = {
            DATES[0]: {"A": bar(DATES[0], high=12.0, close=12.0)},
            DATES[1]: {"A": bar(DATES[1], 0.0, close=float("nan"))},
            DATES[2]: {},
            DATES[3]: {"A": bar(DATES[3], 50.0)},
        }
        self.assertEqual(daily._market_value([holding], bars, DATES[2]), 12000.0)
        bars[DATES[2]] = {"A": bar(DATES[2], 0.0, close=80.0)}
        self.assertEqual(daily._market_value([holding], bars, DATES[2], price_field="open"), 12000.0)
        self.assertEqual(daily._market_value([holding], bars, DATES[2]), 80000.0)

    def test_missing_bar_cannot_exit_until_a_real_bar_resumes(self) -> None:
        bars = self.bars("A", "CALENDAR")
        bars["A"] = bars["A"][:2] + [bar(DATES[4], 8.0)]
        result = self.run_backtest(bars, {DATES[0]: [candidate("A", 0.5)]})
        sale = next(trade for trade in result["trades"] if trade["side"] == "SELL")
        self.assertEqual(sale["trade_date"], DATES[4])
        self.assertEqual(sale["price"], 8.0)
        self.assertEqual(result["curve"][2]["equity"], 10000.0)
        self.assertEqual(result["metrics"]["final_equity"], 9000.0)
        self.assertEqual(result["open_positions"], [])

    def test_missing_or_invalid_bars_do_not_fill_in_any_exit_loop(self) -> None:
        for day_bar in (None, bar(DATES[1], 0.0), bar(DATES[1], float("nan"))):
            for kind in ("intraday", "market", "final"):
                with self.subTest(day_bar=day_bar, kind=kind):
                    holding = position(max_holding_days=1)
                    bars = {DATES[1]: {"A": day_bar} if day_bar else {}}
                    if kind == "intraday":
                        result = daily._process_exits(100.0, [holding], bars, DATES[1], self.profile)
                    elif kind == "market":
                        result = daily._market_exit_positions(100.0, [holding], bars, DATES[1], "risk_off")
                    else:
                        result = daily._force_close_positions(100.0, [holding], bars, DATES[1])
                    self.assertEqual(result, (100.0, [holding], []))

    def test_market_exit_requires_open_even_when_close_is_valid(self) -> None:
        holding = position()
        bars = {DATES[1]: {"A": bar(DATES[1], open=0.0)}}
        result = daily._market_exit_positions(100.0, [holding], bars, DATES[1], "risk_off")
        self.assertEqual(result, (100.0, [holding], []))

    def test_new_purchase_cannot_exit_intraday_or_at_final_liquidation(self) -> None:
        for entry_index in (1, len(DATES) - 1):
            with self.subTest(entry_index=entry_index):
                bars = self.bars("A")
                bars["A"][entry_index].update(low=5.0, high=20.0)
                result = self.run_backtest(bars, {DATES[entry_index - 1]: [candidate("A")]})
                self.assertEqual([trade["side"] for trade in result["trades"]
                                  if trade["trade_date"] == DATES[entry_index]], ["BUY"])
                if entry_index == len(DATES) - 1:
                    self.assertEqual(result["open_positions"][0]["quantity"], 200)

    def test_all_exit_helpers_enforce_entry_date_not_just_held_bars(self) -> None:
        for kind in ("intraday", "market", "final"):
            with self.subTest(kind=kind):
                holding = position(entry_date=DATES[1], held_bars=5, max_holding_days=1,
                                   runner=True, partial_fraction=0.5, partial_take_profit=12.0)
                bars = {DATES[1]: {"A": bar(DATES[1], low=5.0, high=20.0)}}
                if kind == "intraday":
                    result = daily._process_exits(100.0, [holding], bars, DATES[1], self.profile)
                elif kind == "market":
                    result = daily._market_exit_positions(100.0, [holding], bars, DATES[1], "risk_off")
                else:
                    result = daily._force_close_positions(100.0, [holding], bars, DATES[1])
                self.assertEqual(result, (100.0, [holding], []))

    def test_risk_off_uses_prior_day_signal_and_current_open(self) -> None:
        bars = self.bars("A", "B")
        bars["A"][2] = bar(DATES[2], 8.0, high=12.0, close=12.0)
        with patch.object(daily, "_market_regime", side_effect=lambda date, *_: {
            "score": 0.0 if date == DATES[1] else 100.0,
        }):
            result = self.run_backtest(bars, {
                DATES[0]: [candidate("A", 0.9)], DATES[1]: [candidate("B", 0.5)],
            }, market_exit_enabled=True)
        day_trades = [trade for trade in result["trades"] if trade["trade_date"] == DATES[2]]
        self.assertEqual([trade["side"] for trade in day_trades], ["SELL", "BUY"])
        self.assertEqual(day_trades[0]["exit_reason"], "market_risk_off")
        self.assertEqual(day_trades[0]["price"], 8.0)
        self.assertEqual(day_trades[1]["quantity"], 400)

    def test_post_open_risk_signal_cannot_change_same_day_open(self) -> None:
        bars = self.bars("A", "B")
        for symbol in ("A", "B"):
            bars[symbol][3] = bar(DATES[3], 11.0)
        signals = {DATES[0]: [candidate("A", 0.5)], DATES[1]: [candidate("B")]}
        with patch.object(daily, "_market_regime", side_effect=lambda date, _, by_date: {
            "score": 0.0 if by_date[date]["A"]["close"] < 9.0 else 100.0,
        }):
            baseline = self.run_backtest(bars, signals, market_exit_enabled=True, stop_loss_pct=0.8)
            bars["A"][2].update(low=8.0, close=8.0)
            changed = self.run_backtest(bars, signals, market_exit_enabled=True, stop_loss_pct=0.8)
        self.assertEqual(self.buys(baseline, DATES[2]), self.buys(changed, DATES[2]))
        risk_sales = [trade for trade in changed["trades"] if trade.get("exit_reason") == "market_risk_off"]
        self.assertTrue(risk_sales)
        self.assertTrue(all(trade["trade_date"] == DATES[3] for trade in risk_sales))

    def test_trailing_stop_uses_prior_high_and_respects_next_open_gap(self) -> None:
        holding = position(highest_price=12.0, trailing_stop_pct=0.1, take_profit=100.0)
        bars = {DATES[1]: {"A": bar(DATES[1], 12.0, high=20.0, low=11.0, close=19.0)}}
        cash, remaining, trades = daily._process_exits(100.0, [holding], bars, DATES[1], self.profile)
        self.assertEqual(trades, [])
        self.assertEqual(holding.highest_price, 20.0)
        bars[DATES[2]] = {"A": bar(DATES[2], 15.0, high=16.0, low=14.0)}
        _, remaining, trades = daily._process_exits(cash, remaining, bars, DATES[2], self.profile)
        self.assertEqual(remaining, [])
        self.assertEqual(trades[0]["price"], 15.0)
        self.assertEqual(trades[0]["exit_reason"], "trailing_stop")

    def test_cooldown_does_not_look_ahead_or_skip_later_exits(self) -> None:
        bars = self.bars("A", "B", "C")
        bars["A"][2].update(low=5.0)
        bars["B"][3].update(high=16.0)
        result = self.run_backtest(bars, {
            DATES[0]: [candidate("A"), candidate("B")],
            DATES[1]: [candidate("C")],
        }, stop_loss_cooldown_enabled=True, stop_loss_cooldown_count=1)
        self.assertEqual(self.buys(result, DATES[2])[0]["symbol"], "C")
        self.assertTrue(any(trade["symbol"] == "B" and trade["trade_date"] == DATES[3]
                            and trade.get("exit_reason") == "take_profit" for trade in result["trades"]))

    def test_one_day_stop_cooldown_blocks_next_open_then_expires(self) -> None:
        bars = self.bars("A", "B")
        bars["A"][2].update(low=5.0)
        result = self.run_backtest(bars, {
            DATES[0]: [candidate("A")], DATES[2]: [candidate("B")], DATES[3]: [candidate("B")],
        }, stop_loss_cooldown_enabled=True, stop_loss_cooldown_count=1, stop_loss_cooldown_days=1)
        self.assertEqual(self.buys(result, DATES[3]), [])
        self.assertEqual(self.buys(result, DATES[4])[0]["symbol"], "B")

    def test_current_close_drawdown_does_not_cancel_an_earlier_open(self) -> None:
        bars = self.bars("A", "B")
        signals = {DATES[0]: [candidate("A", 0.5)], DATES[1]: [candidate("B")]}
        baseline = self.run_backtest(bars, signals, drawdown_cooldown_enabled=True, stop_loss_pct=0.8)
        bars["A"][2].update(low=5.0, close=5.0)
        changed = self.run_backtest(bars, signals, drawdown_cooldown_enabled=True, stop_loss_pct=0.8)
        self.assertEqual(self.buys(baseline, DATES[2]), self.buys(changed, DATES[2]))

    def test_close_add_on_cannot_reduce_cash_for_earlier_open_buys(self) -> None:
        bars = self.bars("A", "B")
        signals = {DATES[0]: [candidate("A", 0.5, quality_tier="A")], DATES[1]: [candidate("B")]}
        overrides = {"quality_t_enabled": True, "add_on_enabled": True, "tier_a_max_weight": 1.0}
        baseline = self.run_backtest(bars, signals, **overrides)
        bars["A"][2].update(high=14.0, close=14.0)
        changed = self.run_backtest(bars, signals, **overrides)
        self.assertEqual(self.buys(baseline, DATES[2])[0], self.buys(changed, DATES[2])[0])
        day_buys = self.buys(changed, DATES[2])
        self.assertEqual([trade["symbol"] for trade in day_buys], ["B", "A"])
        self.assertEqual(day_buys[1]["reason"], "quality_t_add_on")

    def test_close_add_on_is_not_available_for_same_day_liquidation(self) -> None:
        holding = position(quality_tier="A", max_position_weight=0.8)
        bars = {DATES[0]: {"A": bar(DATES[0])}, DATES[1]: {"A": bar(DATES[1], 12.0)}}
        profile = {**self.profile, "add_on_enabled": True}
        cash, holdings, additions = daily._process_quality_t_management(10000.0, [holding], bars, DATES[1], profile)
        self.assertEqual(len(additions), 1)
        added_quantity = additions[0]["quantity"]
        cash, remaining, sales = daily._force_close_positions(cash, holdings, bars, DATES[1])
        self.assertEqual(sum(trade["quantity"] for trade in sales), 1000)
        self.assertEqual(remaining[0].quantity, added_quantity)
        bars[DATES[2]] = {"A": bar(DATES[2], 12.0)}
        _, remaining, sales = daily._force_close_positions(cash, remaining, bars, DATES[2])
        self.assertEqual(remaining, [])
        self.assertEqual(sales[0]["quantity"], added_quantity)

    def test_t_replacement_lots_cannot_be_sold_twice_same_day(self) -> None:
        holding = position(quality_tier="A", held_bars=1)
        bars = {DATES[0]: {"A": bar(DATES[0])}, DATES[1]: {"A": bar(DATES[1], low=9.5)}}
        cash, holdings, trades = daily._process_quality_t_management(
            10000.0, [holding], bars, DATES[1], {**self.profile, "t_trade_enabled": True},
        )
        t_quantity = next(trade["quantity"] for trade in trades if trade["side"] == "BUY_T")
        _, remaining, sales = daily._force_close_positions(cash, holdings, bars, DATES[1])
        self.assertEqual(sales[0]["quantity"], 1000 - t_quantity)
        self.assertEqual(remaining[0].quantity, t_quantity)

    def test_terminal_equity_reconciles_fees_cash_and_unsettled_holdings(self) -> None:
        tuned = replace(daily.settings, paper_commission_rate=0.001, paper_min_commission=5.0,
                        paper_slippage_pct=0.002, paper_stamp_duty_rate=0.001)
        with patch.object(daily, "settings", tuned):
            result = self.run_backtest(self.bars("A", "B"), {
                DATES[0]: [candidate("A")], DATES[-2]: [candidate("B")],
            })
        cash = 10000.0
        for trade in result["trades"]:
            if trade["side"] == "BUY":
                cash -= trade["amount"] + trade["fee"]
            else:
                cash += trade["amount"] - trade["fee"] - trade["tax"]
            self.assertGreaterEqual(cash, 0.0)
            self.assertGreater(trade["price"], 0.0)
        self.assertAlmostEqual(result["ending_cash"], cash, places=4)
        marked_value = sum(holding["quantity"] * holding["mark_price"] for holding in result["open_positions"])
        self.assertGreater(marked_value, 0.0)
        self.assertAlmostEqual(result["metrics"]["final_equity"], cash + marked_value, places=4)

    def test_results_explicitly_remain_research_only(self) -> None:
        for result in (self.run_backtest(self.bars("A"), {}), daily._empty_result("fixture")):
            with self.subTest(empty=not result["curve"]):
                metadata = result["execution_metadata"]
                self.assertEqual(metadata["validation_status"], "research_only")
                self.assertFalse(metadata["production_validated"])
                self.assertEqual(metadata["price_basis"], "raw_unadjusted")
                self.assertIn("corporate_actions_not_modeled", metadata["limitations"])
                self.assertIn("limit_queue_fills_not_modeled", metadata["limitations"])

    def test_public_result_does_not_call_an_open_only_run_skipped(self) -> None:
        with (
            patch.object(daily, "_load_daily_bars", return_value=self.bars("A")),
            patch.object(daily, "_select_candidates", side_effect=lambda date, *_:
                         [candidate("A")] if date == DATES[-2] else []),
            patch.object(daily, "_write_curve_svg", return_value=Path("fixture.svg")),
            patch.object(daily, "_write_result_json", return_value=Path("fixture.json")),
            patch.object(daily, "log_backtest_run"),
        ):
            result = daily.run_historical_daily_backtest(
                symbols=["A"], start_date=DATES[0], end_date=DATES[-1],
                profile_overrides=self.profile,
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["metrics"]["trade_count"], 0)
        self.assertTrue(result["open_positions"])
        self.assertEqual(result["execution_metadata"]["validation_status"], "research_only")
