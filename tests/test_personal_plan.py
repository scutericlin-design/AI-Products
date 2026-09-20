from __future__ import annotations

from datetime import date

from app.trading.personal_plan import PersonalPlanEngine, default_plan
from app.trading.types import Quote


TODAY = date(2026, 9, 13)


def _ready_plan() -> dict:
    plan = default_plan(TODAY)
    for item in plan["candidates"]:
        if item["tier"] in {"core", "conditional"}:
            item["fundamental_status"] = "pass"
            item["fundamental_as_of"] = TODAY.isoformat()
            item["valuation_as_of"] = TODAY.isoformat()
            item["valuation_pe_ttm"] = max(float(item["max_pe_ttm"] or 1) - 1, 1)
    return plan


def test_core_candidate_generates_lot_rounded_manual_buy() -> None:
    plan = _ready_plan()
    quote = Quote("300750.SZ", "宁德时代", 50.0, 0.5, 10.0, pe_ttm=19.0)

    decision = PersonalPlanEngine().decide(plan, [quote], TODAY)[0]

    assert decision.action == "buy"
    assert decision.raw["planned_shares"] == 100
    assert decision.raw["planned_amount"] == 5000.0
    assert decision.raw["manual_execution_only"] is True


def test_missing_fundamental_data_blocks_buy_plan() -> None:
    plan = default_plan(TODAY)
    plan["candidates"][0]["fundamental_status"] = "needs_refresh"
    plan["candidates"][0]["fundamental_as_of"] = None
    quote = Quote("300750.SZ", "宁德时代", 50.0, 0.5, 10.0, pe_ttm=19.0)

    decision = PersonalPlanEngine().decide(plan, [quote], TODAY)[0]

    assert decision.action == "observe"
    assert "基本面状态未确认" in decision.reason


def test_drawdown_rule_reduces_designated_high_beta_holding() -> None:
    plan = _ready_plan()
    plan["account_value"] = 160000
    plan["portfolio_peak_value"] = 200000
    plan["holdings"] = {"002475.SZ": {"shares": 100}}
    quote = Quote("002475.SZ", "立讯精密", 50.0, -1.0, 10.0, pe_ttm=20.0)

    decisions = PersonalPlanEngine().decide(plan, [quote], TODAY)
    decision = next(item for item in decisions if item.symbol == "002475.SZ")

    assert decision.action == "reduce"
    assert "18%" in decision.reason
