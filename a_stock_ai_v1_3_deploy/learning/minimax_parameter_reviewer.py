from __future__ import annotations

import json
import re
from typing import Any

from ai.llm_failover import LLMFailoverError, request_with_failover
from ai.minimax_client import stock_ai_targets
from app.config import settings
from learning.strategy_params import PARAM_SPECS, get_strategy_params


def review_parameter_proposal(evaluation: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    if not settings.self_learning_ai_review_enabled:
        return {
            "status": "disabled",
            "approved": True,
            "confidence": 1.0,
            "changes": proposal.get("changes") or {},
            "reasoning": "AI review disabled by configuration.",
        }
    if settings.dry_run:
        return _blocked("dry_run", "Dry-run mode blocks AI parameter changes.")
    if not any(target.api_key and target.endpoint for target in stock_ai_targets()):
        return _blocked("missing_config", "Stock LLM configuration missing; keep current parameters.")

    prompt = _build_review_prompt(evaluation, proposal)
    try:
        payload, model, attempts = request_with_failover(
            endpoint=settings.stock_ai_primary_endpoint or "",
            api_key=settings.stock_ai_primary_api_key or "",
            primary_model=settings.stock_ai_primary_model,
            fallback_model=None,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            timeout=45,
            validator=_has_structured_review,
            targets=stock_ai_targets(),
        )
        parsed = _parse_jsonish(_extract_text(payload))
        result = _normalize_review(parsed, proposal, payload)
        result["ai_model"] = model
        result["ai_fallback_used"] = model != settings.stock_ai_primary_model
        result["ai_attempts"] = attempts
        return result
    except LLMFailoverError as exc:
        result = _blocked("error", f"LLM parameter review failed: {exc}")
        result["ai_attempts"] = exc.attempts
        return result


class MiniMaxParameterReviewer:
    def review(self, evaluation: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
        return review_parameter_proposal(evaluation, proposal)


def _build_review_prompt(evaluation: dict[str, Any], proposal: dict[str, Any]) -> str:
    outcomes = list(evaluation.get("outcomes") or [])
    recent_examples = [
        {
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "close_return_pct": item.get("close_return_pct"),
            "max_adverse_pct": item.get("max_adverse_pct"),
            "stop_hit": item.get("stop_hit"),
            "success": item.get("success"),
            "features": item.get("features"),
        }
        for item in outcomes[-20:]
    ]
    current_params = get_strategy_params()
    return f"""
你是A股实时感知系统的参数风控审查AI。
你的任务不是追求收益最大化，而是判断“是否真的需要调参”。如果系统表现稳定，请保持参数不变。

硬规则：
- 只能在白名单参数内给修改建议：{sorted(PARAM_SPECS)}
- 不能建议提高最大仓位，本系统也不会接受仓位参数修改
- 如果样本不足、证据不清、表现稳定，decision 必须为 HOLD
- 如果同意调整，必须小幅、保守、可解释
- 输出必须是 JSON，不要 Markdown

当前参数：
{current_params}

量化复盘指标：
{proposal.get("metrics") or {}}

量化候选建议：
{proposal.get("changes") or {}}

量化理由：
{proposal.get("reason")}

近期样本示例：
{recent_examples}

请输出：
{{
  "decision": "APPROVE 或 HOLD 或 REVISE",
  "confidence": 0.0,
  "changes": {{}},
  "reasoning": "一句话说明"
}}
"""


def _normalize_review(parsed: dict[str, Any], proposal: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    decision = str(parsed.get("decision") or "HOLD").upper()
    confidence = _float(parsed.get("confidence"), 0.0)
    if decision not in {"APPROVE", "HOLD", "REVISE"}:
        decision = "HOLD"
    if confidence < settings.self_learning_ai_min_confidence:
        decision = "HOLD"

    proposed_changes = proposal.get("changes") or {}
    raw_changes = parsed.get("changes") if isinstance(parsed.get("changes"), dict) else {}
    if decision == "APPROVE":
        changes = proposed_changes
    elif decision == "REVISE":
        changes = {name: value for name, value in raw_changes.items() if name in PARAM_SPECS}
    else:
        changes = {}

    return {
        "status": "reviewed",
        "approved": decision in {"APPROVE", "REVISE"} and bool(changes),
        "decision": decision,
        "confidence": confidence,
        "changes": changes,
        "reasoning": str(parsed.get("reasoning") or parsed.get("reason") or ""),
        "raw": raw,
    }


def _blocked(status: str, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "approved": False,
        "decision": "HOLD",
        "confidence": 0.0,
        "changes": {},
        "reasoning": reason,
    }


def _extract_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            return str(message.get("content") or "")
        return str(choices[0].get("text") or "")
    for key in ("reply", "output_text", "content"):
        if payload.get(key):
            return str(payload[key])
    return json.dumps(payload, ensure_ascii=False)


def _parse_jsonish(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
    return {}


def _has_structured_review(payload: dict[str, Any]) -> bool:
    return bool(_parse_jsonish(_extract_text(payload)))


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
