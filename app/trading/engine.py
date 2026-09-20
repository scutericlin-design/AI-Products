from __future__ import annotations

import logging
import traceback
import fcntl
from pathlib import Path
from dataclasses import asdict
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import TradingDecisionLog, TradingEngineRun, TradingPushLog
from app.trading.data import TushareDataLayer
from app.trading.feishu import FeishuNotifier
from app.trading.leader_tracker import LeaderTracker
from app.trading.market_state import MarketStateEngine
from app.trading.minimax_client import MiniMaxDecisionClient
from app.trading.personal_plan import PersonalPlanEngine, PersonalPlanStore
from app.trading.durable_plan import VERSION, propose
from app.trading.durable_data import DurableData
from app.trading.buy_alerts import publish, read_ledger
from app.trading.risk import RiskEngine
from app.trading.types import Decision
from app.trading.utils import to_json, utc_now_naive


logger = logging.getLogger(__name__)


class TradingEngine:
    def __init__(
        self,
        data_layer: TushareDataLayer | None = None,
        market_state_engine: MarketStateEngine | None = None,
        leader_tracker: LeaderTracker | None = None,
        decision_client: MiniMaxDecisionClient | None = None,
        risk_engine: RiskEngine | None = None,
        notifier: FeishuNotifier | None = None,
        personal_plan_engine: PersonalPlanEngine | None = None,
    ) -> None:
        self.data_layer = data_layer or TushareDataLayer()
        self.durable_data = DurableData()
        self.market_state_engine = market_state_engine or MarketStateEngine()
        self.leader_tracker = leader_tracker or LeaderTracker()
        self.decision_client = decision_client or MiniMaxDecisionClient()
        self.risk_engine = risk_engine or RiskEngine()
        self.notifier = notifier or FeishuNotifier()
        self.personal_plan_engine = personal_plan_engine or PersonalPlanEngine()

    def run_cycle(self, trigger_source: str = "scheduler") -> str:
        # The cloud scheduler and manual preview endpoint share one advisory account.
        # Serialize cycles across processes as well as the APScheduler instance.
        with Path('data/personal_buy_cycle.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 'skipped_concurrent_cycle'
            return self._run_cycle(trigger_source)

    def _run_cycle(self, trigger_source: str = "scheduler") -> str:
        cycle_id = uuid4().hex
        db = SessionLocal()
        run = TradingEngineRun(
            cycle_id=cycle_id,
            trigger_source=trigger_source,
            status="running",
            started_at=utc_now_naive(),
        )
        db.add(run)
        db.commit()

        try:
            plan = None
            if settings.trading_personal_plan_enabled:
                plan = PersonalPlanStore().get(db)
                symbols = [item["symbol"] for item in plan["candidates"]]
                quotes = (self.durable_data.fetch_quotes(plan) if plan.get("version") == VERSION
                          else self.data_layer.fetch_quotes(symbols))
                market_state = self.market_state_engine.evaluate(quotes)
                leaders = []
                if plan.get("version") == VERSION:
                    decisions = propose(plan, quotes, read_ledger(db))
                    push_result = publish(db, plan, decisions)
                else:
                    decisions = self.personal_plan_engine.decide(plan, quotes)
                    push_result = self._push_personal_plan_if_needed(db, market_state, decisions)
            else:
                quotes = self.data_layer.fetch_quotes()
                market_state = self.market_state_engine.evaluate(quotes)
                leaders = self.leader_tracker.select(quotes)
                raw_decisions = self.decision_client.decide(market_state, leaders)
                decisions = self.risk_engine.review(raw_decisions, market_state, leaders)
                push_result = self.notifier.send(market_state, decisions)

            self._write_decisions(db, cycle_id, decisions)
            self._write_push(db, cycle_id, push_result)

            run.status = "ok" if quotes else "no_data"
            run.market_phase = market_state.phase
            run.quote_count = len(quotes)
            run.leader_count = len(leaders)
            run.decision_count = len(decisions)
            run.push_status = str(push_result.get("status") or "unknown")
            run.payload_json = to_json(
                {
                    "market_state": asdict(market_state),
                    "quotes": [asdict(item) for item in quotes[:20]],
                    "leaders": [asdict(item) for item in leaders],
                    "personal_plan_version": plan.get("version") if plan else None,
                    "push": push_result,
                }
            )
            run.finished_at = utc_now_naive()
            db.commit()
            logger.info("Trading cycle %s finished with %s decisions.", cycle_id, len(decisions))
            return cycle_id
        except Exception as exc:
            db.rollback()
            run = db.get(TradingEngineRun, run.id)
            if run is not None:
                run.status = "failed"
                run.error_text = f"{exc}\n{traceback.format_exc()}"[:8000]
                run.finished_at = utc_now_naive()
                db.commit()
            logger.exception("Trading cycle %s failed.", cycle_id)
            return cycle_id
        finally:
            db.close()

    def _push_personal_plan_if_needed(self, db: Session, market_state, decisions: list[Decision]) -> dict:
        # Legacy plan delivery stays off after the buy-only migration. This also
        # prevents a downgraded/old plan from resurrecting daily status messages.
        return {"channel": "feishu_personal_plan", "status": "legacy_plan_muted", "payload": {}}

    def _write_decisions(self, db: Session, cycle_id: str, decisions: list[Decision]) -> None:
        for decision in decisions:
            db.add(
                TradingDecisionLog(
                    cycle_id=cycle_id,
                    symbol=decision.symbol,
                    name=decision.name,
                    action=decision.action,
                    confidence=decision.confidence,
                    target_weight=decision.target_weight,
                    risk_level=decision.risk_level,
                    reason=decision.reason,
                    risk_flags=",".join(decision.risk_flags) if decision.risk_flags else None,
                    payload_json=to_json(asdict(decision)),
                )
            )
        db.commit()

    def _write_push(self, db: Session, cycle_id: str, push_result: dict) -> None:
        db.add(
            TradingPushLog(
                cycle_id=cycle_id,
                channel=str(push_result.get("channel") or self.notifier.channel),
                status=str(push_result.get("status") or "unknown"),
                response_code=push_result.get("response_code"),
                response_text=push_result.get("response_text"),
                payload_json=to_json(push_result.get("payload")),
            )
        )
        db.commit()
