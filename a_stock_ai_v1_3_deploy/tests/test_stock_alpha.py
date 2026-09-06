from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from stock_alpha import ModelConfig, build_targets, build_weight_deltas, prepare_model_data, run_backtest
from stock_alpha.backtest import _sell_tax_rate


def sample(names: int = 30, days: int = 175, start: str = "2023-01-02"):
    calendar = pd.bdate_range(start, periods=days).strftime("%Y%m%d").tolist()
    records, financials = [], []
    for index in range(names):
        symbol = f"{600000 + index:06d}.SH"
        t = np.arange(days)
        changes = 0.0004 + index * 0.00004 + (0.001 + index * 0.00025) * np.sin(t * 0.6 + index)
        prices = (10 + index) * np.cumprod(1 + changes)
        previous = np.r_[prices[0], prices[:-1]]
        for day, close, prior in zip(calendar, prices, previous):
            records.append({
                "ts_code": symbol, "trade_date": day, "open": float(prior * 1.0001),
                "high": float(max(close, prior) * 1.02), "low": float(min(close, prior) * 0.98),
                "close": float(close), "pre_close": float(prior), "vol": 1e6,
                "amount": 1e8, "adj_factor": 1.0, "pe_ttm": 8 + index * 0.5, "pb": 1 + index * 0.04,
            })
        report_date = (pd.Timestamp(start) - pd.Timedelta(days=100)).strftime("%Y%m%d")
        announcement = (pd.Timestamp(start) - pd.Timedelta(days=60)).strftime("%Y%m%d")
        financials.append({"ts_code": symbol, "ann_date": announcement, "end_date": report_date,
                           "roe": 10 + index * 0.3, "or_yoy": index + 1, "ocfps": 0.5 + index * 0.1,
                           "debt_to_assets": 20 + index})
    memberships = pd.DataFrame({"ts_code": [row["ts_code"] for row in financials], "trade_date": calendar[0]})
    return pd.DataFrame(records), pd.DataFrame(financials), memberships, calendar


class StockAlphaModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars, cls.financials, cls.memberships, cls.calendar = sample()
        cls.as_of = cls.calendar[140]
        cls.config = ModelConfig()

    def plan(self, bars=None, financials=None, memberships=None, weights=None, config=None, as_of=None):
        return build_targets(
            self.bars if bars is None else bars,
            self.financials if financials is None else financials,
            self.memberships if memberships is None else memberships,
            as_of or self.as_of, weights or {}, config or self.config,
        )

    def test_defaults_are_frozen_and_inputs_unchanged(self):
        before = self.bars.copy(deep=True)
        with self.assertRaises(FrozenInstanceError):
            self.config.variant = "trend_scaled"
        plan = self.plan()
        pd.testing.assert_frame_equal(before, self.bars)
        self.assertEqual(plan["status"], "ok")
        json.dumps(plan, allow_nan=False)

    def test_future_perturbation_and_truncation(self):
        expected = self.plan()
        bars = self.bars.copy()
        bars.loc[bars.trade_date > self.as_of, ["close", "adj_factor", "pe_ttm"]] = 1e20
        future = self.financials.assign(ann_date=self.calendar[145], end_date=self.calendar[141], roe=1e9)
        financials = pd.concat([self.financials, future], ignore_index=True)
        memberships = pd.concat([self.memberships, self.memberships.assign(trade_date=self.calendar[145])])
        self.assertEqual(expected, self.plan(bars, financials, memberships))
        truncated = self.plan(self.bars.loc[self.bars.trade_date <= self.as_of])
        self.assertEqual(expected, truncated)

    def test_announcements_are_strict_and_report_vintages_ordered(self):
        same_day = self.financials.assign(ann_date=self.as_of, end_date=self.calendar[130], roe=999)
        self.assertEqual(self.plan(), self.plan(financials=pd.concat([self.financials, same_day])))
        older_correction = self.financials.assign(ann_date=self.calendar[139], end_date="20210101", roe=999)
        self.assertEqual(self.plan(), self.plan(financials=pd.concat([self.financials, older_correction])))
        blocked = self.plan(financials=same_day)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(set(blocked["diagnostics"]["exclusions"].values()), {"no_strictly_prior_financial"})

    def test_missing_and_stale_financials_never_score_as_good(self):
        financials = self.financials.copy()
        financials.loc[0, "ocfps"] = np.nan
        financials.loc[1, "end_date"] = "20200101"
        plan = self.plan(financials=financials)
        excluded = plan["diagnostics"]["exclusions"]
        self.assertEqual(excluded["600000.SH"], "missing_or_invalid_financial")
        self.assertEqual(excluded["600001.SH"], "stale_financial")
        self.assertNotIn("600000.SH", {row["ts_code"] for row in plan["scores"]})

    def test_invalid_adjustment_valuation_stale_bar_and_nonstock_excluded(self):
        bars = self.bars.copy()
        bars.loc[(bars.ts_code == "600000.SH") & (bars.trade_date == self.calendar[100]), "adj_factor"] = np.nan
        bars.loc[(bars.ts_code == "600001.SH") & (bars.trade_date == self.as_of), "pb"] = -1
        bars = bars.loc[~((bars.ts_code == "600002.SH") & (bars.trade_date == self.as_of))]
        memberships = pd.concat([self.memberships, pd.DataFrame({"ts_code": ["510300.SH", "000906.SH", "430001.BJ"], "trade_date": self.calendar[0]})])
        reasons = self.plan(bars, memberships=memberships)["diagnostics"]["exclusions"]
        self.assertEqual(reasons["600000.SH"], "missing_or_invalid_close_or_adjustment")
        self.assertEqual(reasons["600001.SH"], "nonpositive_or_missing_valuation")
        self.assertEqual(reasons["600002.SH"], "stale_bar")
        for symbol in ("510300.SH", "000906.SH", "430001.BJ"):
            self.assertEqual(reasons[symbol], "not_supported_a_share_stock")

    def test_insufficient_data_fails_closed(self):
        plan = self.plan(as_of=self.calendar[124])
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["targets"], [])
        self.assertEqual(plan["weights"], {})
        self.assertEqual(set(plan["diagnostics"]["exclusions"].values()), {"insufficient_bars"})

    def test_missing_recent_session_is_not_a_one_day_return(self):
        bars = self.bars.loc[~((self.bars.ts_code == "600000.SH") & (self.bars.trade_date == self.calendar[139]))]
        self.assertEqual(self.plan(bars)["diagnostics"]["exclusions"]["600000.SH"], "missing_required_session")

    def test_missing_schema_and_duplicate_bars(self):
        with self.assertRaisesRegex(ValueError, "missing columns"):
            self.plan(bars=self.bars.drop(columns="adj_factor"))
        duplicate = pd.concat([self.bars, self.bars.iloc[[0]]])
        self.assertEqual(self.plan(duplicate)["diagnostics"]["exclusions"]["600000.SH"], "duplicate_bar")

    def test_latest_known_membership_is_a_snapshot_not_union(self):
        snapshot = self.memberships.iloc[:15].assign(trade_date=self.calendar[135])
        plan = self.plan(memberships=pd.concat([self.memberships, snapshot]))
        self.assertEqual(plan["diagnostics"]["universe_count"], 15)
        self.assertTrue(set(plan["weights"]).issubset(set(snapshot.ts_code)))
        self.assertEqual(plan["diagnostics"]["membership_date"], self.calendar[135])

    def test_four_factors_equal_ranked_and_quality_not_spot_heuristic(self):
        base = self.plan()
        altered = self.bars.copy()
        altered[["open", "high", "low", "vol", "amount"]] = 999999
        self.assertEqual(base, self.plan(altered))
        for row in base["scores"]:
            self.assertAlmostEqual(row["score"], 25 * sum(row[key] for key in ("quality", "value", "momentum_rank", "lowvol")))
        symbol = base["scores"][0]["ts_code"]
        raw = self.bars.loc[(self.bars.ts_code == symbol) & (self.bars.trade_date <= self.as_of)].tail(126)
        row = next(row for row in base["scores"] if row["ts_code"] == symbol)
        self.assertAlmostEqual(row["momentum"], raw.close.iloc[-6] / raw.close.iloc[0] - 1)

    def test_positive_cashflow_magnitude_does_not_change_quality(self):
        baseline = {row["ts_code"]: row for row in self.plan()["scores"]}
        financials = self.financials.copy()
        financials.loc[0, "ocfps"] *= 10000
        changed = {row["ts_code"]: row for row in self.plan(financials=financials)["scores"]}
        for symbol in baseline:
            self.assertEqual(baseline[symbol]["quality"], changed[symbol]["quality"])
            self.assertEqual(baseline[symbol]["score"], changed[symbol]["score"])
        self.assertEqual(changed["600000.SH"]["cashflow_sign_score"], 1.0)
        financials.loc[0, "ocfps"] = -1
        negative = next(row for row in self.plan(financials=financials)["scores"] if row["ts_code"] == "600000.SH")
        self.assertEqual(negative["cashflow_sign_score"], 0.0)
        self.assertLess(negative["quality"], changed["600000.SH"]["quality"])

    def test_retention_buffer_preserves_top24_and_drops_below(self):
        ranked = self.plan()["scores"]
        retained = {row["ts_code"]: 0.06 for row in ranked[12:24]}
        plan = self.plan(weights=retained)
        self.assertEqual(set(plan["weights"]), set(retained))
        outside = ranked[24]["ts_code"]
        self.assertNotIn(outside, self.plan(weights={outside: 0.06})["weights"])

    def test_inversevol_caps_and_variants_share_scores(self):
        balanced = self.plan()
        trend = self.plan(config=replace(self.config, variant="trend_scaled"))
        self.assertEqual(balanced["scores"], trend["scores"])
        for plan in (balanced, trend):
            self.assertEqual(len(plan["targets"]), 12)
            self.assertLessEqual(max(plan["weights"].values()), 0.08 + 1e-12)
            self.assertLessEqual(sum(plan["weights"].values()), 0.8 + 1e-12)
            free = [row for row in plan["targets"] if row["weight"] < 0.08 - 1e-10]
            products = [row["weight"] * max(row["volatility"], self.config.volatility_floor) for row in free]
            if products:
                self.assertLess(max(products) - min(products), 1e-10)
        self.assertLessEqual(sum(trend["weights"].values()), sum(balanced["weights"].values()))

    def test_trend_scales_falling_market_without_changing_scores(self):
        bars = self.bars.copy()
        ordinals = {date: index for index, date in enumerate(self.calendar)}
        bars["close"] = 100 * np.exp(-0.002 * bars.trade_date.map(ordinals))
        balanced = self.plan(bars)
        trend = self.plan(bars, config=replace(self.config, variant="trend_scaled"))
        self.assertEqual(trend["scores"], balanced["scores"])
        self.assertEqual(trend["diagnostics"]["slow_breadth"], 0)
        self.assertAlmostEqual(trend["diagnostics"]["risk_scale"], 0.25)
        self.assertAlmostEqual(sum(trend["weights"].values()), 0.2)

    def test_capped_budget_and_input_order_independence(self):
        plan = self.plan(config=replace(self.config, max_names=8))
        self.assertAlmostEqual(sum(plan["weights"].values()), 0.64)
        shuffled = self.plan(self.bars.sample(frac=1, random_state=1),
                             self.financials.sample(frac=1, random_state=2),
                             self.memberships.sample(frac=1, random_state=3))
        self.assertEqual(self.plan(), shuffled)

    def test_correlation_guard_limits_identical_return_cluster(self):
        bars = self.bars.copy()
        path = self.bars.loc[self.bars.ts_code == "600000.SH"].set_index("trade_date").close
        bars["close"] = bars.trade_date.map(path)
        plan = self.plan(bars)
        self.assertEqual(len(plan["targets"]), 3)
        self.assertTrue(plan["diagnostics"]["correlation_rejections"])
        self.assertFalse(plan["diagnostics"]["industry_neutral"])
        changed = bars.copy()
        changed.loc[changed.trade_date > self.as_of, "close"] *= 100
        self.assertEqual(plan, self.plan(changed))

    def test_shared_band_preserves_exit_and_risk_cap_reductions(self):
        decision = build_weight_deltas({"A": 0.05}, {"A": 0.055}, self.config)
        self.assertEqual(decision["deltas"]["A"], 0)
        self.assertIn("A", decision["suppressed"])
        self.assertEqual(build_weight_deltas({"A": 0.005}, {}, self.config)["deltas"]["A"], -0.005)
        self.assertLess(build_weight_deltas({"A": 0.085}, {"A": 0.08}, self.config)["deltas"]["A"], 0)
        self.assertLess(build_weight_deltas({"A": 0.05}, {"A": 0.045}, self.config, risk_reduction=True)["deltas"]["A"], 0)
        self.assertLess(build_weight_deltas({"A": 0.05}, {"A": 0.045}, self.config, exposure_limit=0.045)["deltas"]["A"], 0)

    def test_prepared_and_direct_model_agree(self):
        prepared = prepare_model_data(self.bars, self.financials, self.memberships, [self.as_of], self.config)
        direct = self.plan()
        cached = build_targets(self.bars, self.financials, self.memberships, self.as_of, {}, self.config, prepared=prepared)
        self.assertEqual(direct["status"], cached["status"])
        self.assertEqual(set(direct["weights"]), set(cached["weights"]))
        self.assertEqual(direct["diagnostics"]["exclusions"], cached["diagnostics"]["exclusions"])
        for symbol in direct["weights"]:
            self.assertAlmostEqual(direct["weights"][symbol], cached["weights"][symbol], places=10)


class StockAlphaReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars, cls.financials, cls.memberships, cls.calendar = sample(days=175)
        cls.config = ModelConfig()
        cls.start, cls.end = cls.calendar[140], cls.calendar[151]

    def replay(self, bars=None, end=None, config=None, cost_multiplier=1):
        return run_backtest(self.bars if bars is None else bars, self.financials, self.memberships,
                            self.calendar, self.start, end or self.end, config or self.config,
                            cost_multiplier=cost_multiplier)

    def test_replay_contract_weekly_next_open_t1_and_endday(self):
        result = self.replay()
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["promotable"])
        self.assertEqual(result["execution_mode"], "research_proxy")
        self.assertEqual(len(result["daily_curve"]), 12)
        self.assertEqual(result["daily_curve"][-1]["date"], self.end)
        self.assertEqual(result["daily_curve"][0]["cash"], 1e6)
        self.assertEqual([plan["as_of"] for plan in result["diagnostics"]["plans"]], [self.calendar[i] for i in (140, 145, 150)])
        buys = {}
        for order in result["orders"]:
            self.assertEqual(self.calendar.index(order["date"]), self.calendar.index(order["signal_date"]) + 1)
            self.assertNotIn("quantity", order)
            if order["status"] != "filled":
                self.assertNotIn("notional", order)
                continue
            self.assertGreater(order["notional"], 0)
            if order["side"] == "BUY":
                buys.setdefault(order["ts_code"], order["date"])
            else:
                self.assertLess(buys[order["ts_code"]], order["date"])
        self.assertTrue(all(row["cash"] >= 0 and row["names"] <= 12 for row in result["daily_curve"]))
        json.dumps(result, allow_nan=False)

    def test_execution_day_ohlc_does_not_change_open_orders(self):
        day = self.calendar[141]
        expected = self.replay(end=day)
        bars = self.bars.copy()
        bars.loc[bars.trade_date == day, ["close", "high", "low", "vol", "amount", "pe_ttm", "pb"]] *= 4
        observed = self.replay(bars, end=day)
        self.assertEqual(expected["orders"], observed["orders"])
        self.assertNotEqual(expected["daily_curve"][-1]["equity"], observed["daily_curve"][-1]["equity"])

    def test_replay_future_perturbation_and_prefix_invariance(self):
        expected = self.replay()
        bars = self.bars.copy()
        bars.loc[bars.trade_date > self.end, ["close", "open", "adj_factor"]] = 1e12
        self.assertEqual(expected, self.replay(bars))
        prefix_end = self.calendar[146]
        prefix = self.replay(end=prefix_end)
        self.assertEqual(prefix["daily_curve"], [row for row in expected["daily_curve"] if row["date"] <= prefix_end])
        self.assertEqual(prefix["orders"], [row for row in expected["orders"] if row["date"] <= prefix_end])

    def test_cost_stress_reduces_fixed_path_nav(self):
        config = replace(self.config, rebalance_sessions=100)
        free = self.replay(config=config, cost_multiplier=0)
        base = self.replay(config=config)
        stressed = self.replay(config=config, cost_multiplier=2)
        self.assertEqual(free["metrics"]["costs"], 0)
        self.assertGreater(free["daily_curve"][-1]["equity"], base["daily_curve"][-1]["equity"])
        self.assertGreater(base["daily_curve"][-1]["equity"], stressed["daily_curve"][-1]["equity"])
        self.assertAlmostEqual(stressed["metrics"]["costs"], 2 * base["metrics"]["costs"], delta=1)

    def test_adjusted_units_prevent_false_exright_loss(self):
        event_day = self.calendar[143]
        baseline = self.replay()
        bars = self.bars.copy()
        event = bars.trade_date >= event_day
        bars.loc[event, ["open", "high", "low", "close", "pre_close"]] /= 2
        bars.loc[event, "adj_factor"] *= 2
        observed = self.replay(bars)
        self.assertEqual(baseline["orders"], observed["orders"])
        self.assertEqual(baseline["daily_curve"], observed["daily_curve"])
        self.assertFalse(observed["promotable"])

    def test_missing_final_bar_carries_mark_without_zero_fill(self):
        baseline = self.replay(config=replace(self.config, rebalance_sessions=100))
        symbol = baseline["orders"][0]["ts_code"]
        bars = self.bars.loc[~((self.bars.ts_code == symbol) & (self.bars.trade_date == self.end))]
        result = self.replay(bars, config=replace(self.config, rebalance_sessions=100))
        self.assertIn(symbol, result["daily_curve"][-1]["stale_symbols"])
        self.assertGreater(result["daily_curve"][-1]["equity"], 0)
        self.assertTrue(any(row["reason"] == "stale_mark" for row in result["diagnostics"]["warnings"]))

    def test_missing_open_is_unfilled_and_invalid_adjustment_blocks(self):
        baseline = self.replay()
        symbol = baseline["orders"][0]["ts_code"]
        first_open = self.calendar[141]
        bars = self.bars.copy()
        bars.loc[(bars.ts_code == symbol) & (bars.trade_date == first_open), "open"] = np.nan
        result = self.replay(bars)
        rejected = [order for order in result["orders"] if order["date"] == first_open and order["ts_code"] == symbol]
        self.assertEqual(rejected[0]["status"], "unfilled")
        self.assertNotIn("notional", rejected[0])
        bars.loc[(bars.ts_code == symbol) & (bars.trade_date == first_open), "adj_factor"] = np.nan
        result = self.replay(bars)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["diagnostics"]["fatal_errors"])

    def test_tax_change(self):
        self.assertEqual(_sell_tax_rate("20230825"), 0.001)
        self.assertEqual(_sell_tax_rate("20230828"), 0.0005)

    def test_sell_ledger_uses_tax_on_execution_date(self):
        symbol = "600000.SH"
        sessions = ["20230823", "20230824", "20230825", "20230828", "20230829"]
        bars = pd.concat([self.bars.iloc[[0]].assign(trade_date=date) for date in sessions])
        def planner(bars, financials, memberships, as_of, weights, config, **kwargs):
            chosen = {symbol: 0.08} if as_of in {"20230823", "20230825"} else {}
            return {"status": "ok", "as_of": as_of, "weights": chosen, "targets": [], "diagnostics": {}}
        with patch("stock_alpha.backtest.build_targets", side_effect=planner):
            result = run_backtest(bars, self.financials, self.memberships, sessions, sessions[0], sessions[-1],
                                  replace(self.config, rebalance_sessions=1))
        sells = [order for order in result["orders"] if order["side"] == "SELL" and order["status"] == "filled"]
        self.assertEqual([order["sell_tax_rate"] for order in sells], [0.001, 0.0005])
        for order in sells:
            self.assertAlmostEqual(order["tax"], order["notional"] * order["sell_tax_rate"])

    def test_minimum_fees_never_overdraw_small_cash(self):
        result = run_backtest(self.bars, self.financials, self.memberships, self.calendar, self.start, self.end,
                              replace(self.config, min_commission=1000), initial_cash=100)
        self.assertTrue(all(row["cash"] >= 0 for row in result["daily_curve"]))
        self.assertFalse(any(order["status"] == "filled" for order in result["orders"]))

    def test_missing_whole_calendar_session_blocks_but_keeps_final_mark(self):
        bars = self.bars.loc[self.bars.trade_date != self.end]
        result = self.replay(bars)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["daily_curve"][-1]["date"], self.end)
        self.assertTrue(any(error["reason"] == "missing_calendar_session_bars" for error in result["diagnostics"]["fatal_errors"]))

    def test_annual_rows_use_prior_year_closing_equity(self):
        bars, financials, memberships, calendar = sample(days=160, start="2023-06-05")
        result = run_backtest(bars, financials, memberships, calendar, calendar[140], calendar[-1], self.config)
        self.assertEqual([row["year"] for row in result["annual"]], [2023, 2024])
        self.assertEqual(result["annual"][1]["start_equity"], result["annual"][0]["end_equity"])
        compounded = np.prod([1 + row["return_pct"] / 100 for row in result["annual"]])
        self.assertAlmostEqual(compounded, 1 + result["metrics"]["return_pct"] / 100)

    def test_no_eligible_data_stays_cash_and_is_blocked(self):
        result = self.replay(bars=self.bars.loc[self.bars.trade_date >= self.start])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["orders"], [])
        self.assertTrue(all(row["equity"] == 1e6 for row in result["daily_curve"]))

    def test_pending_exits_do_not_allow_more_than_max_names(self):
        symbols = sorted(self.memberships.ts_code)[:24]
        def planner(bars, financials, memberships, as_of, weights, config, **kwargs):
            chosen = symbols[:12] if as_of == self.start else symbols[12:]
            return {"status": "ok", "as_of": as_of, "weights": dict.fromkeys(chosen, 0.8 / 12), "targets": [], "diagnostics": {}}
        bars = self.bars.copy()
        unavailable = bars.ts_code.isin(symbols[:12]) & (bars.trade_date == self.calendar[146])
        bars.loc[unavailable, "open"] = np.nan
        with patch("stock_alpha.backtest.build_targets", side_effect=planner):
            result = self.replay(bars)
        self.assertTrue(all(row["names"] <= 12 for row in result["daily_curve"]))
        self.assertTrue(any(order.get("reason") == "max_names_with_unfilled_exits" for order in result["orders"]))

    def test_invalid_calendar_and_configuration(self):
        with self.assertRaisesRegex(ValueError, "calendar"):
            run_backtest(self.bars, self.financials, self.memberships, self.calendar[::-1], self.start, self.end, self.config)
        with self.assertRaises(ValueError):
            ModelConfig(variant="optimized")
        with self.assertRaises(ValueError):
            ModelConfig(commission_bps=-1)

    def test_shared_preparation_matches_uncached_and_rejects_mutated_inputs(self):
        signals = [date for date in self.calendar if self.start <= date <= self.end][::5]
        prepared = prepare_model_data(self.bars, self.financials, self.memberships, signals, self.config, end=self.end)
        for variant in ("balanced", "trend_scaled"):
            config = replace(self.config, variant=variant)
            for costs in (1, 2):
                cached = run_backtest(self.bars, self.financials, self.memberships, self.calendar, self.start, self.end,
                                      config, cost_multiplier=costs, prepared=prepared)
                self.assertEqual(cached, self.replay(config=config, cost_multiplier=costs))
        changed = self.bars.copy()
        changed.loc[0, "close"] *= 2
        with self.assertRaisesRegex(ValueError, "source/end mismatch"):
            run_backtest(changed, self.financials, self.memberships, self.calendar, self.start, self.end,
                          self.config, prepared=prepared)

    def test_band_records_skips_without_fabricated_fills(self):
        symbol = "600000.SH"
        def planner(bars, financials, memberships, as_of, weights, config, **kwargs):
            chosen = {symbol: 0.05 if as_of == self.start else 0.055}
            return {"status": "ok", "as_of": as_of, "weights": chosen, "targets": [], "diagnostics": {}}
        with patch("stock_alpha.backtest.build_targets", side_effect=planner):
            result = self.replay()
        skips = [order for order in result["orders"] if order["status"] == "skipped"]
        self.assertTrue(skips)
        self.assertEqual(result["diagnostics"]["band_skips"], len(skips))
        self.assertTrue(all(order["reason"] == "within_nav_band" and "notional" not in order for order in skips))


if __name__ == "__main__":
    unittest.main()
