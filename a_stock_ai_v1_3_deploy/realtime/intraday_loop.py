from __future__ import annotations

import logging
import time
from uuid import uuid4

from ai.minimax_client import call_minimax
from app.config import settings
from engine.leader_tracker import get_leader
from engine.market_state import get_market_state
from engine.ranking_engine import build_recommendation_bundle
from engine.risk_engine import risk_check
from engine.sentiment_engine import get_market_sentiment
from learning.strategy_params import get_strategy_param_snapshot
from notify.feishu import send_feishu_execution_report
from realtime.market_stream import MarketStream
from scheduler.trading_calendar import current_trading_window
from storage.logger import finish_cycle, log, log_decisions, log_push, log_strategy_cycle, start_cycle
from strategies.ensemble_engine import MultiStrategyEngine
from strategies.primary_strategy import apply_primary_strategy, primary_strategy_summary


logger = logging.getLogger(__name__)


def run_once(trigger_source: str = "scheduler") -> str:
    cycle_id = uuid4().hex
    start_cycle(cycle_id, trigger_source)
    try:
        # 1. 拉数据
        strategy_params = get_strategy_param_snapshot()
        market_stream = MarketStream()
        quotes, sentiment_quotes, selection_universe = market_stream.load_cycle_quotes()
        if not sentiment_quotes:
            logger.warning("market sentiment sample unavailable; falling back to selection quotes")
            sentiment_quotes = quotes

        # 2. 更新状态
        state = get_market_state(sentiment_quotes)
        state["selection_quote_count"] = len(quotes)
        state["sentiment_quote_count"] = len(sentiment_quotes)

        # 3. 更新市场情绪
        market_sentiment = get_market_sentiment(sentiment_quotes, state)
        state = {
            **state,
            "raw_sentiment": state.get("sentiment"),
            "sentiment": market_sentiment.get("sentiment_score", state.get("sentiment")),
            "sentiment_status": market_sentiment.get("sentiment_status"),
            "risk_appetite": market_sentiment.get("risk_appetite"),
        }

        # 4. 更新龙头
        leader = get_leader(quotes)

        # 5. 候选池排序与可买性过滤
        recommendation_bundle = build_recommendation_bundle(state, leader, quotes, market_sentiment)

        # 6. 多策略组合。shadow 模式只记录并驱动模拟盘，保留原有飞书决策。
        multi_strategy = MultiStrategyEngine().build(
            state=state,
            sentiment=market_sentiment,
            leader=leader,
            quotes=quotes,
            robust_bundle=recommendation_bundle,
        )
        active_portfolio = (
            multi_strategy.get("portfolio_signal")
            if settings.primary_strategy_id == "multi_strategy" and settings.multi_strategy_mode == "active"
            else None
        )

        # 7. 调MiniMax。激活多策略后，AI 只能确认系统组合，不能新增标的。
        ai_result = call_minimax(
            state,
            leader,
            recommendation_bundle,
            market_sentiment,
            portfolio_plan=active_portfolio,
        )

        # 8. 风控处理
        final_signal = apply_primary_strategy(risk_check(ai_result))
        primary_strategy = primary_strategy_summary(final_signal)

        # 9. 先执行模拟盘。只有实际成交的订单才会进入飞书成交回报。
        paper_result: dict[str, object] = {
            "status": "skipped_paper_trading_disabled",
            "no_real_orders": True,
        }
        if settings.paper_trading_enabled:
            try:
                from simulation.paper_trading import run_paper_simulation_once

                paper_result = run_paper_simulation_once(
                    cycle_id=cycle_id,
                    final_signal=final_signal,
                    primary_strategy=primary_strategy,
                    market_prices={quote.symbol: quote.price for quote in quotes if quote.price > 0},
                )
                logger.info(
                    "paper simulation finished: cycle=%s status=%s filled=%s",
                    cycle_id,
                    paper_result.get("status"),
                    (paper_result.get("summary") or {}).get("filled_orders", 0),
                )
            except Exception as exc:
                paper_result = {
                    "status": "failed",
                    "reason": str(exc),
                    "no_real_orders": True,
                }
                logger.warning("paper simulation failed for cycle %s: %s", cycle_id, exc, exc_info=True)

        # 10. 不推送观察池、HOLD、未成交或心跳；只发送已成交的模拟单。
        push_result = send_feishu_execution_report(paper_result)

        # 11. 写日志
        cycle_payload = {
            "cycle_id": cycle_id,
            "engine": {
                "name": settings.engine_name,
                "positioning": settings.engine_positioning,
                "value": settings.value_statement,
            },
            "quotes_count": len(quotes),
            "selection_universe": selection_universe,
            "sentiment_quotes_count": len(sentiment_quotes),
            "sentiment_quote_sources": sorted({quote.source for quote in sentiment_quotes}),
            "strategy_params": strategy_params,
            "state": state,
            "market_sentiment": market_sentiment,
            "leader": leader,
            "recommendation_bundle": recommendation_bundle,
            "multi_strategy": multi_strategy,
            "primary_strategy": primary_strategy,
            "ai_result": ai_result,
            "final_signal": final_signal,
            "paper_simulation": paper_result,
            "push": push_result,
        }
        log(cycle_payload)
        log_decisions(cycle_id, final_signal)
        log_strategy_cycle(cycle_id, multi_strategy)
        log_push(cycle_id, push_result)
        finish_cycle(
            cycle_id,
            status="ok" if quotes else "no_data",
            market_phase=str(state.get("phase") or "unknown"),
            quote_count=len(quotes),
            leader_count=len(leader.get("candidates") or []) if leader.get("stock") else 0,
            decision_count=max(1, int(final_signal.get("recommendation_count") or 0)),
            push_status=push_result.get("status"),
            payload=cycle_payload,
        )
        logger.info(
            "perception cycle %s finished: status=%s signal=%s recommendations=%s",
            cycle_id,
            "ok" if quotes else "no_data",
            final_signal.get("signal"),
            final_signal.get("recommendation_count", 0),
        )
        return cycle_id
    except Exception as exc:
        logger.exception("cycle %s failed", cycle_id)
        finish_cycle(cycle_id, status="failed", error_text=str(exc))
        return cycle_id


def run_loop() -> None:
    while True:
        window = current_trading_window()
        if window.is_open:
            run_once(trigger_source="loop")
        else:
            logger.info("skip loop cycle: phase=%s reason=%s", window.phase, window.reason)
        time.sleep(settings.loop_seconds)


class IntradayLoop:
    def run_once(self, trigger_source: str = "scheduler") -> str:
        return run_once(trigger_source)
