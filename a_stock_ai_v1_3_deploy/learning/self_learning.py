from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from app.config import settings
from learning.minimax_parameter_reviewer import review_parameter_proposal
from learning.outcome_evaluator import evaluate_recent_outcomes
from learning.parameter_optimizer import optimize_parameters
from learning.strategy_params import apply_strategy_param_changes, get_strategy_param_snapshot
from notify.feishu import send_feishu_text
from storage.logger import log_learning_run, log_strategy_param_version


logger = logging.getLogger(__name__)


PARAM_LABELS = {
    "CONFIDENCE_THRESHOLD": "推荐置信度门槛",
    "SENTIMENT_MIN_BUY_SCORE": "最低买入情绪分",
    "SENTIMENT_RISK_OFF_SCORE": "风险关闭情绪分",
    "SENTIMENT_PANIC_THRESHOLD": "恐慌防守阈值",
    "MIN_TURNOVER_YI": "最低成交额门槛",
    "MAX_RECOMMEND_PCT_CHANGE": "最大推荐涨幅",
    "BUY_RANGE_PULLBACK_PCT": "买入区间回撤要求",
    "MAX_CHASE_PCT": "最高追价幅度",
    "STOP_LOSS_PCT": "止损幅度",
    "MAX_PUSH_STOCKS": "每轮最多推荐数",
    "SENTIMENT_LOW_COVERAGE_COUNT": "情绪低覆盖样本数",
    "SENTIMENT_FULL_COVERAGE_COUNT": "情绪充分覆盖样本数",
}

PARAM_CAPABILITIES = {
    "CONFIDENCE_THRESHOLD": "提升推荐筛选严格度，减少低确定性信号",
    "SENTIMENT_MIN_BUY_SCORE": "提升市场情绪确认能力，减少弱势行情出手",
    "SENTIMENT_RISK_OFF_SCORE": "提升风险关闭敏感度，更早进入防守",
    "SENTIMENT_PANIC_THRESHOLD": "优化恐慌环境识别，避免错误时间交易",
    "MIN_TURNOVER_YI": "提升流动性过滤能力，减少买不进和冲击成本",
    "MAX_RECOMMEND_PCT_CHANGE": "提升追高控制能力，减少高位接力风险",
    "BUY_RANGE_PULLBACK_PCT": "提升买点耐心，倾向等回落而不是追价",
    "MAX_CHASE_PCT": "提升追价纪律，降低冲动成交风险",
    "STOP_LOSS_PCT": "优化单票风险边界，让失败信号更可控",
    "MAX_PUSH_STOCKS": "优化消息质量，减少不必要推送",
    "SENTIMENT_LOW_COVERAGE_COUNT": "提升情绪样本可信度判断",
    "SENTIMENT_FULL_COVERAGE_COUNT": "提升全局情绪覆盖要求",
}


def run_self_learning(trigger_source: str = "scheduler") -> dict[str, Any]:
    run_id = uuid4().hex
    try:
        evaluation = evaluate_recent_outcomes(settings.self_learning_lookback_days)
        proposal = optimize_parameters(evaluation)
        ai_review: dict[str, Any] = {"status": "not_required", "approved": False}
        applied: dict[str, Any] = {"applied": False}
        status = str(proposal.get("status") or "unknown")

        if proposal.get("status") == "proposed":
            ai_review = review_parameter_proposal(evaluation, proposal)
            proposal["ai_review"] = ai_review
            if ai_review.get("approved"):
                proposal["changes"] = ai_review.get("changes") or {}
                proposal["reason"] = (
                    f"{proposal.get('reason')}; MiniMax复核：{ai_review.get('reasoning')}"
                )
                proposal["should_apply"] = settings.self_learning_apply_changes
                status = "ai_approved"
            else:
                proposal["should_apply"] = False
                status = f"ai_hold_{ai_review.get('status')}"

        if (
            settings.primary_strategy_id == "hybrid_alpha"
            and settings.hybrid_alpha_lock_parameters
            and proposal.get("should_apply")
        ):
            proposal["should_apply"] = False
            proposal["reason"] = (
                f"{proposal.get('reason')}; Hybrid Alpha 主策略参数已冻结，仅记录复盘建议，不自动改动。"
            )
            status = "primary_profile_locked_observe"

        if proposal.get("should_apply"):
            applied = apply_strategy_param_changes(
                proposal.get("changes") or {},
                reason=str(proposal.get("reason") or ""),
                metrics=proposal.get("metrics") or {},
            )
            status = "applied" if applied.get("applied") else "proposal_rejected_by_bounds"
            if applied.get("applied"):
                log_strategy_param_version(
                    version=int(applied.get("version") or 0),
                    status=status,
                    reason=str(proposal.get("reason") or ""),
                    params=applied.get("params") or {},
                    changes=applied.get("changes") or {},
                    metrics=proposal.get("metrics") or {},
                )
                if settings.self_learning_notify:
                    _notify_learning_update(run_id, proposal, applied)

        log_learning_run(
            run_id=run_id,
            trigger_source=trigger_source,
            status=status,
            sample_count=int(evaluation.get("sample_count") or 0),
            evaluated_count=int(evaluation.get("evaluated_count") or 0),
            metrics=proposal.get("metrics") or {},
            proposal=proposal,
            applied=applied,
        )
        logger.info(
            "self-learning run %s finished: status=%s samples=%s evaluated=%s",
            run_id,
            status,
            evaluation.get("sample_count"),
            evaluation.get("evaluated_count"),
        )
        return {
            "run_id": run_id,
            "status": status,
            "sample_count": evaluation.get("sample_count"),
            "evaluated_count": evaluation.get("evaluated_count"),
            "metrics": proposal.get("metrics") or {},
            "ai_review": ai_review,
            "changes": applied.get("changes") or proposal.get("changes") or {},
            "params": get_strategy_param_snapshot(),
        }
    except Exception as exc:
        logger.exception("self-learning run %s failed", run_id)
        log_learning_run(
            run_id=run_id,
            trigger_source=trigger_source,
            status="failed",
            sample_count=0,
            evaluated_count=0,
            metrics={},
            proposal={},
            applied={},
            error_text=str(exc),
        )
        return {"run_id": run_id, "status": "failed", "error": str(exc)}


class SelfLearningJob:
    def run(self, trigger_source: str = "scheduler") -> dict[str, Any]:
        return run_self_learning(trigger_source)


def _notify_learning_update(run_id: str, proposal: dict[str, Any], applied: dict[str, Any]) -> None:
    metrics = proposal.get("metrics") or {}
    changes = applied.get("changes") or {}
    ai_review = proposal.get("ai_review") if isinstance(proposal.get("ai_review"), dict) else {}
    capability_lines = _capability_lines(changes)
    lines = [
        f"{settings.engine_name} 系统自我升级通知",
        f"学习ID：{run_id}",
        f"参数版本：v{applied.get('version')}",
        "状态：已完成参数优化，下一轮盘中感知将自动使用新参数",
        f"样本：{metrics.get('evaluated_count', 0)} 条",
        (
            "表现："
            f"胜率 {float(metrics.get('win_rate') or 0):.1%} | "
            f"均收盘收益 {float(metrics.get('avg_close_return_pct') or 0):.2f}% | "
            f"止损率 {float(metrics.get('stop_hit_rate') or 0):.1%}"
        ),
        f"升级原因：{proposal.get('reason')}",
        (
            "MiniMax复核："
            f"{ai_review.get('decision', 'unknown')} | "
            f"置信度 {float(ai_review.get('confidence') or 0):.0%} | "
            f"{ai_review.get('reasoning') or '无额外说明'}"
        ),
        "本次升级内容：",
    ]
    for name, item in changes.items():
        lines.append(_format_change_line(name, item))
    if capability_lines:
        lines.append("能力提升方向：")
        lines.extend(f"- {line}" for line in capability_lines)
    lines.append("说明：这是参数层升级，不涉及程序代码改动；如后续表现稳定，系统会保持不动。")
    send_feishu_text("\n".join(lines))


def _format_change_line(name: str, item: dict[str, Any]) -> str:
    label = PARAM_LABELS.get(name, name)
    direction = _direction(item.get("old"), item.get("new"))
    return f"- {label}（{name}）：{item.get('old')} -> {item.get('new')}，{direction}"


def _capability_lines(changes: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for name in changes:
        capability = PARAM_CAPABILITIES.get(name)
        if capability and capability not in seen:
            seen.append(capability)
    return seen


def _direction(old_value: Any, new_value: Any) -> str:
    try:
        old = float(old_value)
        new = float(new_value)
    except (TypeError, ValueError):
        return "参数已更新"
    if new > old:
        return "参数上调"
    if new < old:
        return "参数下调"
    return "参数保持"
