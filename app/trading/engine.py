from __future__ import annotations

import logging
import traceback
from dataclasses import asdict
from uuid import uuid4

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import TradingDecisionLog, TradingEngineRun, TradingPushLog
from app.trading.data import TushareDataLayer
from app.trading.feishu import FeishuNotifier
from app.trading.leader_tracker import LeaderTracker
from app.trading.market_state import MarketStateEngine
from app.trading.minimax_client import MiniMaxDecisionClient
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
    ) -> None:
        self.data_layer = data_layer or TushareDataLayer()
        self.market_state_engine = market_state_engine or MarketStateEngine()
        self.leader_tracker = leader_tracker or LeaderTracker()
        self.decision_client = decision_client or MiniMaxDecisionClient()
        self.risk_engine = risk_engine or RiskEngine()
        self.notifier = notifier or FeishuNotifier()

    def run_cycle(self, trigger_source: str = "scheduler") -> str:
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
                channel=self.notifier.channel,
                status=str(push_result.get("status") or "unknown"),
                response_code=push_result.get("response_code"),
                response_text=push_result.get("response_text"),
                payload_json=to_json(push_result.get("payload")),
            )
        )
        db.commit()
