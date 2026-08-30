from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from qgarp_strategy.config import load_settings
from qgarp_strategy.models import DailyBar, FundamentalSnapshot, Instrument
from qgarp_strategy.notify import send_qgarp_execution_report
from qgarp_strategy.paper import QGARPPaperRunner
from qgarp_strategy.research_profiles import RESEARCH_PROFILES, apply_research_profile
from qgarp_strategy.robustness_research import _holdout_verdict
from qgarp_strategy.storage import QGARPStore
from qgarp_strategy.strategy import QGARPStrategy
from qgarp_strategy.v3_research import QGARPv3Research, QGARPv3Settings
from qgarp_strategy.v4_research import QGARPv4Research
from qgarp_strategy.v4_factor_state_ledger import FrozenFactorPortfolioLedger
from qgarp_strategy.v4_signal_diagnostics import _features
from qgarp_strategy.runner import QGARPStrategyRunner


class QGARPStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = replace(load_settings(), storage_dir=root, db_path=root / "qgarp.sqlite", reports_dir=root / "reports", universe_limit=20, max_names=10, push_enabled=False, dry_run=True)
        self.settings.ensure_dirs()
        self.store = QGARPStore(self.settings.db_path)
        self.instruments = [Instrument(symbol=f"000{i:03d}.SZ", name=f"测试{i}", industry="测试行业A" if i < 6 else "测试行业B", list_date="20100101") for i in range(1, 13)]
        self.histories = {item.symbol: _bars(item.symbol, 10 + index * 0.2) for index, item in enumerate(self.instruments)}
        self.fundamentals = {item.symbol: FundamentalSnapshot(symbol=item.symbol, report_end_date="20251231", available_at="20260331", roe=10 + index, revenue_yoy=8 + index, profit_yoy=12 + index, operating_cashflow_per_share=1.0 + index * 0.1, debt_to_assets=45 - index, source="test") for index, item in enumerate(self.instruments)}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_strategy_generates_complete_buy_plan(self) -> None:
        plan = QGARPStrategy(self.settings).decide(as_of="20260731", instruments=self.instruments, histories=self.histories, fundamentals=self.fundamentals, benchmark=_bars("000906.SH", 20))
        self.assertEqual(plan["strategy_id"], "qgarp_alpha")
        self.assertGreater(len(plan["recommendations"]), 0)
        candidate = plan["recommendations"][0]
        for field in ("symbol", "name", "trigger_price", "target_weight", "stop_loss", "take_profit", "reasoning"):
            self.assertIn(field, candidate)
        self.assertLessEqual(candidate["target_weight"], self.settings.paper_max_position_pct)

    def test_paper_orders_are_isolated_and_push_only_filled(self) -> None:
        plan = QGARPStrategy(self.settings).decide(as_of="20260731", instruments=self.instruments, histories=self.histories, fundamentals=self.fundamentals, benchmark=_bars("000906.SH", 20))
        result = QGARPPaperRunner(self.settings, self.store).run(plan, "2026-08-01")
        self.assertTrue(result["no_real_orders"])
        self.assertGreater(len(result["orders"]), 0)
        order = result["orders"][0]
        self.assertEqual(order["strategy_id"], "qgarp_alpha")
        self.assertEqual(send_qgarp_execution_report({"orders": []}, self.settings)["status"], "skipped_no_qgarp_filled_orders")
        self.assertEqual(send_qgarp_execution_report(result, self.settings)["status"], "disabled")

    def test_small_industry_does_not_overwrite_other_industry_ranks(self) -> None:
        def make_item(index: int, industry: str) -> dict:
            return {
                "instrument": Instrument(symbol=f"9{index:05d}.SZ", name=f"样本{index}", industry=industry),
                "raw": {
                    "quality": float(index), "growth": float(index * 2), "value": float(index * 3),
                    "momentum": float(index * 4), "low_vol": float(index * 5), "liquidity": float(index * 6),
                },
            }

        large_only = [make_item(index, "大行业") for index in range(1, 6)]
        mixed = [make_item(index, "大行业") for index in range(1, 6)] + [make_item(6, "小行业"), make_item(7, "小行业")]
        baseline = {item["instrument"].symbol: item["factor_score"].total_score for item in QGARPStrategy(self.settings)._rank_and_score(large_only)}
        observed = {item["instrument"].symbol: item["factor_score"].total_score for item in QGARPStrategy(self.settings)._rank_and_score(mixed)}
        for symbol, score in baseline.items():
            self.assertEqual(observed[symbol], score)

    def test_adjustment_factor_batch_is_resumable(self) -> None:
        self.store.upsert_daily_bars([
            DailyBar(symbol="000001.SZ", trade_date="20260105", open=10, high=10, low=10, close=10, pre_close=10, pct_chg=0, volume=1, amount=1, source="test"),
            DailyBar(symbol="000002.SZ", trade_date="20260105", open=10, high=10, low=10, close=10, pre_close=10, pct_chg=0, volume=1, amount=1, source="test"),
        ])
        self.assertEqual(self.store.adjustment_dates_needing_enrichment("20260105", "20260105", 10), ["20260105"])
        self.assertEqual(self.store.upsert_adjustment_factors_for_date("20260105", {"000001.SZ": 2.0, "000002.SZ": 2.0}), 2)
        self.assertEqual(self.store.adjustment_dates_needing_enrichment("20260105", "20260105", 10), [])

    def test_named_profile_has_distinct_reproducible_defaults(self) -> None:
        with patch.dict("os.environ", {"QGARP_STRATEGY_PROFILE": "quality_value_defensive"}, clear=False):
            defensive = load_settings()
        self.assertEqual(defensive.strategy_profile, "quality_value_defensive")
        self.assertGreater(defensive.factor_weights["low_vol"], self.settings.factor_weights["low_vol"])

    def test_research_profile_is_explicit_and_does_not_mutate_base_settings(self) -> None:
        candidate = apply_research_profile(self.settings, "quality_value_low_turnover")
        self.assertEqual(candidate.strategy_version, RESEARCH_PROFILES["quality_value_low_turnover"]["strategy_version"])
        self.assertEqual(candidate.rebalance_months, 2)
        self.assertEqual(self.settings.rebalance_months, 1)

    def test_walk_forward_acceptance_requires_each_holdout_to_clear_excess_return(self) -> None:
        good = [{"status": "ok", "metrics": {"excess_return_pct": 1.0, "sharpe": 0.5, "max_drawdown_pct": -12.0}}, {"status": "ok", "metrics": {"excess_return_pct": 0.1, "sharpe": 0.4, "max_drawdown_pct": -15.0}}]
        failed = [{"status": "ok", "metrics": {"excess_return_pct": 1.0, "sharpe": 1.0, "max_drawdown_pct": -5.0}}, {"status": "ok", "metrics": {"excess_return_pct": -0.1, "sharpe": 1.0, "max_drawdown_pct": -5.0}}]
        self.assertTrue(_holdout_verdict(good)["passed"])
        self.assertFalse(_holdout_verdict(failed)["passed"])

    def test_zero_weight_factor_is_not_a_hidden_confirmation_gate(self) -> None:
        settings = replace(self.settings, factor_growth_weight=0.0, factor_momentum_weight=0.0, factor_confirmation_count=2)
        strategy = QGARPStrategy(settings)
        item = {
            "instrument": self.instruments[0],
            "bars": self.histories[self.instruments[0].symbol],
            "factor_score": type("Score", (), {"quality": 80, "earnings": 10, "valuation": 80, "trend": 1, "low_vol": 80, "liquidity": 50, "total_score": 80})(),
        }
        selected = strategy._portfolio([item], 0.8)
        self.assertEqual(len(selected), 1)

    def test_v3_research_plan_is_orderless_and_requires_complete_financials(self) -> None:
        adjusted = {
            symbol: [replace(bar, adj_factor=1.0) for bar in bars]
            for symbol, bars in self.histories.items()
        }
        plan = QGARPv3Research(QGARPv3Settings()).plan(
            as_of="20260731",
            instruments=self.instruments,
            histories=adjusted,
            fundamentals=self.fundamentals,
            benchmark=_bars("000906.SH", 20),
        )
        self.assertEqual(plan["execution_mode"], "research_only_no_paper_no_push")
        self.assertTrue(plan["recommendations"])
        self.assertNotIn("orders", plan)
        candidate = plan["recommendations"][0]
        self.assertIn(candidate["action"], {"BUY", "HOLD"})
        for field in ("symbol", "name", "trigger_price", "target_weight", "stop_loss", "take_profit", "reasoning"):
            self.assertIn(field, candidate)
        incomplete = dict(self.fundamentals)
        incomplete[self.instruments[0].symbol] = replace(incomplete[self.instruments[0].symbol], roe=None)
        ranked = QGARPv3Research(QGARPv3Settings()).rank(
            as_of="20260731", instruments=self.instruments, histories=adjusted, fundamentals=incomplete,
        )
        self.assertNotIn(self.instruments[0].symbol, {row["instrument"].symbol for row in ranked})

    def test_v4_factor_momentum_engine_is_research_only(self) -> None:
        adjusted = {symbol: [replace(bar, adj_factor=1.0) for bar in bars] for symbol, bars in self.histories.items()}
        plan = QGARPv4Research().plan(
            as_of="20260731", instruments=self.instruments, histories=adjusted,
            fundamentals=self.fundamentals, benchmark=_bars("000906.SH", 20),
        )
        self.assertEqual(plan["execution_mode"], "research_only_no_paper_no_push")
        self.assertTrue(plan["recommendations"])
        candidate = plan["recommendations"][0]
        self.assertEqual(candidate["strategy_id"], "qgarp_v4_frozen_factor_state_research")
        self.assertIn("factor_momentum", candidate["factor_scores"])
        self.assertIn("stock_relative_momentum", candidate["factor_scores"])
        self.assertIn("factor_state", plan)

    def test_frozen_factor_ledger_never_uses_same_date_portfolio_return(self) -> None:
        rows = []
        for index, instrument in enumerate(self.instruments):
            rows.append({
                "instrument": instrument,
                "bars": [replace(bar, adj_factor=1.0) for bar in self.histories[instrument.symbol]],
                "ranks": {key: index / 11 for key in ("quality", "growth", "value", "momentum", "low_vol")},
            })
        ledger = FrozenFactorPortfolioLedger(lookback_months=3)
        ledger.add("20260131", rows)
        prices = {symbol: bars[-1].close * 1.1 for symbol, bars in self.histories.items()}
        self.assertEqual(ledger.state("20260131", prices)["source"], "insufficient_frozen_history")
        state = ledger.state("20260228", prices)
        self.assertEqual(state["source"], "frozen_factor_portfolios")
        self.assertEqual(state["formation_dates"], ["20260131"])

    def test_v49_industry_residual_features_use_only_as_of_bars(self) -> None:
        rows = []
        for index, instrument in enumerate(self.instruments[:6]):
            bars = [replace(bar, adj_factor=1.0) for bar in self.histories[instrument.symbol]]
            if index == 0:
                bars[-1] = replace(bars[-1], close=bars[-1].close * 0.80)
            if index == 5:
                bars[-1] = replace(bars[-1], close=bars[-1].close * 1.20)
            rows.append({"instrument": instrument, "bars": bars})
        features = _features(rows, benchmark_r60=0.30)
        self.assertEqual(len(features), 6)
        self.assertTrue(all("forward_excess_return" not in row for row in features))
        self.assertTrue(all(row["industry_strength_60"] < 0 for row in features))
        self.assertLess(features[0]["residual_momentum_60"], features[-1]["residual_momentum_60"])
        self.assertGreater(features[0]["short_reversal_5"], 0)
        self.assertLess(features[-1]["short_reversal_5"], 0)

    def test_runner_refuses_paper_execution(self) -> None:
        result = QGARPStrategyRunner(settings=self.settings, store=self.store).paper_once()
        self.assertEqual(result["status"], "disabled_research_only")
        self.assertEqual(result["orders"], [])

    def test_research_lock_ignores_execution_environment_flags(self) -> None:
        with patch.dict("os.environ", {"QGARP_PAPER_ENABLED": "true", "QGARP_PUSH_ENABLED": "true", "QGARP_DRY_RUN": "false"}, clear=False):
            locked = load_settings()
        self.assertFalse(locked.paper_enabled)
        self.assertFalse(locked.push_enabled)
        self.assertTrue(locked.dry_run)


def _bars(symbol: str, start: float) -> list[DailyBar]:
    values = []
    for index in range(180):
        price = start * (1 + index * 0.004)
        values.append(DailyBar(symbol=symbol, trade_date=f"2026{(index // 20) + 4:02d}{(index % 20) + 1:02d}", open=price * 0.995, high=price * 1.01, low=price * 0.99, close=price, pre_close=price * 0.996, pct_chg=0.4, volume=1_000_000, amount=120_000_000, pe_ttm=15 + index * 0.01, pb=2.0, source="test"))
    return values


if __name__ == "__main__":
    unittest.main()
