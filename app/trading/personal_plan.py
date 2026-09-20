from __future__ import annotations

import json
import math
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import PlatformSetting
from app.services.timezone import now_beijing
from app.trading.types import Decision, Quote
from app.trading.utils import normalize_symbol


PERSONAL_PLAN_KEY = "personal_5y_investment_plan_v1"
PERSONAL_PLAN_VERSION = "personal_5y_v1"
PLAN_ACTIONS = {"buy", "add", "reduce", "exit"}
RESEARCH_SNAPSHOT_DATE = "2026-09-13"


def default_plan(today: date | None = None) -> dict[str, Any]:
    """Return the user's approved 20万元, five-year research plan.

    Amounts are caps, not orders. The plan remains deliberately incomplete until
    fresh valuation and fundamental timestamps are supplied by the data pipeline.
    """
    started = (today or now_beijing().date()).isoformat()
    plan = {
        "version": PERSONAL_PLAN_VERSION,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "plan_started_at": started,
        "account_value": 200000.0,
        "available_cash": 200000.0,
        "portfolio_peak_value": 200000.0,
        "max_equity_amount": 120000.0,
        "low_volatility_reserve": 80000.0,
        "max_portfolio_drawdown": 0.30,
        "valuation_max_age_days": 3,
        "fundamental_max_age_days": 100,
        "stages": [
            {"after_days": 0, "completion": 0.25},
            {"after_days": 60, "completion": 0.50},
            {"after_days": 150, "completion": 0.75},
            {"after_days": 240, "completion": 1.00},
        ],
        "holdings": {},
        "candidates": [
            _candidate("300750.SZ", "宁德时代", "core", 25000, 20.0, 100, "动力电池、储能与海外业务；跟踪单位盈利与海外政策。"),
            _candidate("000333.SZ", "美的集团", "core", 20000, 16.0, 100, "现金回报与全球制造能力；核对扣非与套保差异。"),
            _candidate("600406.SH", "国电南瑞", "core", 16000, 22.0, 100, "电网升级；核对订单交付、回款与经营现金流。"),
            _candidate("002475.SZ", "立讯精密", "core", 15000, 25.0, 100, "精密制造延伸；核对扣非、现金流和并购整合。"),
            _candidate("600276.SH", "恒瑞医药", "conditional", 14000, 35.0, 100, "创新药销售与商业化；授权收入不可单独作为买入依据。"),
            _candidate("300124.SZ", "汇川技术", "observe", 0, 25.0, 100, "等待归母利润和经营现金流恢复。"),
            _candidate("600660.SH", "福耀玻璃", "observe", 0, 18.0, 100, "等待主营利润恢复，单独审视汇兑影响。"),
            _candidate("688012.SH", "中微公司", "defer", 0, 0, 200, "高估值，待主营正常化利润与估值同时满足后再研究。"),
            _candidate("002371.SZ", "北方华创", "defer", 0, 0, 100, "等待毛利率企稳和估值回到可承受区间。"),
            _candidate("300308.SZ", "中际旭创", "defer", 0, 0, 100, "等待现金流、应收和存货与利润同步改善。"),
            _candidate("510300.SH", "沪深300ETF", "core_etf", 30000, None, 100, "宽基配置，用于分散单一公司风险。"),
        ],
    }
    # These are the dated research values already approved for this plan. They
    # expire quickly by design; a later run blocks purchases until the pool is
    # refreshed with a new source date and current valuation.
    snapshot_pe = {
        "300750.SZ": 18.0,
        "000333.SZ": 14.8,
        "600406.SH": 21.3,
        "002475.SZ": 24.1,
        "600276.SH": 36.7,
    }
    for item in plan["candidates"]:
        if item["symbol"] in snapshot_pe:
            item.update(
                {
                    "valuation_pe_ttm": snapshot_pe[item["symbol"]],
                    "valuation_as_of": RESEARCH_SNAPSHOT_DATE,
                    "fundamental_status": "pass",
                    "fundamental_as_of": RESEARCH_SNAPSHOT_DATE,
                }
            )
    return plan


def _candidate(
    symbol: str,
    name: str,
    tier: str,
    target_amount: float,
    max_pe_ttm: float | None,
    lot_size: int,
    thesis: str,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name,
        "tier": tier,
        "target_amount": float(target_amount),
        "max_pe_ttm": max_pe_ttm,
        "lot_size": lot_size,
        "thesis": thesis,
        # These must be refreshed from a traceable source before a stock can
        # become actionable. Empty values intentionally block a buy proposal.
        "valuation_pe_ttm": None,
        "valuation_as_of": None,
        "fundamental_status": "needs_refresh",
        "fundamental_as_of": None,
    }


class PersonalPlanStore:
    def get(self, db: Session) -> dict[str, Any]:
        item = db.get(PlatformSetting, PERSONAL_PLAN_KEY)
        if item is None:
            plan = default_plan()
            db.add(PlatformSetting(key=PERSONAL_PLAN_KEY, value=json.dumps(plan, ensure_ascii=False, sort_keys=True)))
            db.commit()
            return plan
        try:
            plan = json.loads(item.value)
        except (TypeError, json.JSONDecodeError):
            plan = default_plan()
        return validate_plan(plan)

    def put(self, db: Session, plan: dict[str, Any]) -> dict[str, Any]:
        normalized = validate_plan(plan)
        normalized["updated_at"] = datetime.now().isoformat(timespec="seconds")
        item = db.get(PlatformSetting, PERSONAL_PLAN_KEY)
        value = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        if item is None:
            db.add(PlatformSetting(key=PERSONAL_PLAN_KEY, value=value))
        else:
            item.value = value
        db.commit()
        return normalized


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("计划必须是 JSON 对象")
    from app.trading.durable_plan import VERSION, validate
    if plan.get('version') == VERSION:
        return validate(plan)
    merged = default_plan()
    merged.update({key: value for key, value in plan.items() if key in merged})
    if float(merged["account_value"]) <= 0:
        raise ValueError("账户总资产必须大于 0")
    if float(merged["available_cash"]) < 0:
        raise ValueError("可用资金不能小于 0")
    if float(merged["max_equity_amount"]) > float(merged["account_value"]):
        raise ValueError("权益上限不能超过账户总资产")
    if not isinstance(merged["candidates"], list) or not merged["candidates"]:
        raise ValueError("股票池不能为空")
    symbols: set[str] = set()
    for item in merged["candidates"]:
        if not isinstance(item, dict):
            raise ValueError("股票池项目格式错误")
        item["symbol"] = normalize_symbol(item.get("symbol", ""))
        if not item["symbol"] or item["symbol"] in symbols:
            raise ValueError("股票代码为空或重复")
        symbols.add(item["symbol"])
        item["target_amount"] = max(float(item.get("target_amount") or 0), 0)
        item["lot_size"] = max(int(item.get("lot_size") or 100), 1)
        item["tier"] = str(item.get("tier") or "observe")
    target_total = sum(float(item["target_amount"]) for item in merged["candidates"])
    if target_total > float(merged["max_equity_amount"]) + 0.01:
        raise ValueError("股票池目标金额超过权益上限")
    return merged


class PersonalPlanEngine:
    """Deterministic, manual-only plan generator for the private stock pool."""

    def decide(self, plan: dict[str, Any], quotes: list[Quote], today: date | None = None) -> list[Decision]:
        current_day = today or now_beijing().date()
        quote_by_symbol = {quote.symbol: quote for quote in quotes}
        completion = self._stage_completion(plan, current_day)
        drawdown = self._drawdown(plan)
        decisions = []
        for candidate in plan["candidates"]:
            decisions.append(
                self._one_decision(plan, candidate, quote_by_symbol.get(candidate["symbol"]), completion, drawdown, current_day)
            )
        return decisions

    def _one_decision(
        self,
        plan: dict[str, Any],
        candidate: dict[str, Any],
        quote: Quote | None,
        completion: float,
        drawdown: float,
        today: date,
    ) -> Decision:
        common = {
            "plan_version": plan["version"],
            "tier": candidate["tier"],
            "stage_completion": completion,
            "portfolio_drawdown": drawdown,
            "manual_execution_only": True,
        }
        if candidate["tier"] in {"observe", "defer"}:
            return self._decision(candidate, "observe", 0, "观察名单：不生成委托；" + candidate["thesis"], "normal", raw=common)
        if quote is None or quote.price <= 0:
            return self._decision(candidate, "observe", 0, "行情数据缺失或价格无效，暂停生成买卖计划。", "high", ("missing_quote",), common)
        if drawdown >= 0.24:
            return self._risk_decision(candidate, quote, "reduce", "组合回撤达到24%，将直接持股降至总资产25%以内。", "high", common)
        if drawdown >= 0.18:
            if candidate["symbol"] in {"002475.SZ", "600276.SH"}:
                return self._risk_decision(candidate, quote, "reduce", "组合回撤达到18%，高波动仓位减持三分之一。", "high", common)
            return self._decision(candidate, "hold", 0, "组合回撤达到18%，停止新增个股仓位。", "high", ("portfolio_drawdown_18pct",), common)
        if drawdown >= 0.12:
            return self._decision(candidate, "hold", 0, "组合回撤达到12%，暂停所有个股加仓。", "elevated", ("portfolio_drawdown_12pct",), common)
        if candidate["tier"] != "core_etf":
            quality_issue = self._quality_block(candidate, today, plan)
            if quality_issue:
                return self._decision(candidate, "observe", 0, quality_issue, "elevated", ("stale_or_missing_fundamental",), common)
            valuation_issue = self._valuation_block(candidate, quote, today, plan)
            if valuation_issue:
                return self._decision(candidate, "observe", 0, valuation_issue, "elevated", ("valuation_gate",), common)
        if quote.pct_change > 3.0:
            return self._decision(candidate, "observe", 0, "当日上涨超过3%，不追价，等待下一交易日复核。", "elevated", ("no_chase",), common)

        holding_shares = float((plan.get("holdings") or {}).get(candidate["symbol"], {}).get("shares") or 0)
        target_amount = float(candidate["target_amount"]) * completion
        current_value = holding_shares * quote.price
        needed_amount = max(target_amount - current_value, 0)
        lots = math.floor(needed_amount / (quote.price * int(candidate["lot_size"])))
        planned_shares = lots * int(candidate["lot_size"])
        planned_amount = planned_shares * quote.price
        raw = {
            **common,
            "price": quote.price,
            "pe_ttm": quote.pe_ttm,
            "held_shares": holding_shares,
            "current_value": round(current_value, 2),
            "target_amount_this_stage": round(target_amount, 2),
            "planned_shares": planned_shares,
            "planned_amount": round(planned_amount, 2),
            "max_limit_price": round(quote.price * 1.003, 2),
        }
        if planned_shares <= 0:
            return self._decision(
                candidate,
                "hold" if holding_shares else "observe",
                float(candidate["target_amount"]) / float(plan["account_value"]),
                "当前阶段目标不足一手，或已达到阶段仓位；保留资金等待下一阶段。",
                "normal",
                raw=raw,
            )
        if planned_amount > float(plan["available_cash"]):
            return self._decision(candidate, "observe", 0, "可用资金不足；请在账户状态中同步现金后再生成计划。", "high", ("insufficient_cash",), raw)
        action = "add" if holding_shares else "buy"
        return self._decision(
            candidate,
            action,
            float(candidate["target_amount"]) / float(plan["account_value"]),
            f"满足本阶段风控条件。人工限价买入不高于{raw['max_limit_price']:.2f}元，数量{planned_shares:.0f}股；下单后请同步持仓与现金。",
            "normal",
            raw=raw,
        )

    def _risk_decision(self, candidate: dict[str, Any], quote: Quote, action: str, reason: str, risk: str, common: dict[str, Any]) -> Decision:
        shares = float(common.get("held_shares") or 0)
        return self._decision(candidate, action, 0, reason, risk, ("portfolio_drawdown_limit",), {**common, "price": quote.price, "held_shares": shares})

    def _quality_block(self, candidate: dict[str, Any], today: date, plan: dict[str, Any]) -> str | None:
        if candidate.get("fundamental_status") != "pass":
            return "基本面状态未确认通过；需更新最新定期报告中的主营利润、经营现金流和重大风险。"
        if self._age_days(candidate.get("fundamental_as_of"), today) > int(plan["fundamental_max_age_days"]):
            return "基本面数据已过期；更新定期报告后再生成买入计划。"
        return None

    def _valuation_block(self, candidate: dict[str, Any], quote: Quote, today: date, plan: dict[str, Any]) -> str | None:
        max_pe = candidate.get("max_pe_ttm")
        current_pe = quote.pe_ttm if quote.pe_ttm is not None else candidate.get("valuation_pe_ttm")
        if max_pe is None:
            return None
        if current_pe is None or float(current_pe) <= 0:
            return "PE TTM 数据缺失；不以旧估值生成买入计划。"
        if quote.pe_ttm is None and self._age_days(candidate.get("valuation_as_of"), today) > int(plan["valuation_max_age_days"]):
            return "估值快照已过期；需要最新PE TTM后再生成买入计划。"
        if float(current_pe) > float(max_pe):
            return f"PE TTM {float(current_pe):.1f}倍，高于计划上限{float(max_pe):.1f}倍。"
        return None

    def _drawdown(self, plan: dict[str, Any]) -> float:
        peak = max(float(plan.get("portfolio_peak_value") or 0), 0)
        current = max(float(plan.get("account_value") or 0), 0)
        return max((peak - current) / peak, 0) if peak else 0.0

    def _stage_completion(self, plan: dict[str, Any], today: date) -> float:
        try:
            started = date.fromisoformat(str(plan["plan_started_at"]))
        except (KeyError, TypeError, ValueError):
            started = today
        elapsed = max((today - started).days, 0)
        completion = 0.0
        for stage in plan["stages"]:
            if elapsed >= int(stage["after_days"]):
                completion = max(completion, float(stage["completion"]))
        return min(max(completion, 0.0), 1.0)

    @staticmethod
    def _age_days(value: Any, today: date) -> int:
        try:
            return max((today - date.fromisoformat(str(value)[:10])).days, 0)
        except (TypeError, ValueError):
            return 999999

    @staticmethod
    def _decision(
        candidate: dict[str, Any],
        action: str,
        target_weight: float,
        reason: str,
        risk_level: str,
        flags: tuple[str, ...] = (),
        raw: dict[str, Any] | None = None,
    ) -> Decision:
        return Decision(
            symbol=candidate["symbol"],
            name=candidate["name"],
            action=action,
            confidence=0.90 if action in PLAN_ACTIONS else 0.75,
            target_weight=round(target_weight, 4),
            reason=reason,
            risk_level=risk_level,
            risk_flags=flags,
            raw=raw or {},
        )
