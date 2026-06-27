from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
PARAMS_FILE = settings.storage_dir / "strategy_params.json"


@dataclass(frozen=True)
class ParamSpec:
    name: str
    attr: str
    kind: str
    minimum: float
    maximum: float
    max_step: float


PARAM_SPECS: dict[str, ParamSpec] = {
    "CONFIDENCE_THRESHOLD": ParamSpec("CONFIDENCE_THRESHOLD", "confidence_threshold", "float", 0.58, 0.78, 0.02),
    "SENTIMENT_MIN_BUY_SCORE": ParamSpec("SENTIMENT_MIN_BUY_SCORE", "sentiment_min_buy_score", "float", 50, 72, 2),
    "SENTIMENT_RISK_OFF_SCORE": ParamSpec("SENTIMENT_RISK_OFF_SCORE", "sentiment_risk_off_score", "float", 34, 50, 2),
    "SENTIMENT_PANIC_THRESHOLD": ParamSpec("SENTIMENT_PANIC_THRESHOLD", "sentiment_panic_threshold", "float", 60, 85, 2),
    "MIN_TURNOVER_YI": ParamSpec("MIN_TURNOVER_YI", "min_turnover_yi", "float", 1.0, 8.0, 0.3),
    "MAX_RECOMMEND_PCT_CHANGE": ParamSpec(
        "MAX_RECOMMEND_PCT_CHANGE", "max_recommend_pct_change", "float", 5.0, 8.8, 0.5
    ),
    "BUY_RANGE_PULLBACK_PCT": ParamSpec("BUY_RANGE_PULLBACK_PCT", "buy_range_pullback_pct", "float", 0.004, 0.02, 0.002),
    "MAX_CHASE_PCT": ParamSpec("MAX_CHASE_PCT", "max_chase_pct", "float", 0.003, 0.018, 0.002),
    "STOP_LOSS_PCT": ParamSpec("STOP_LOSS_PCT", "stop_loss_pct", "float", 0.025, 0.055, 0.003),
    "MAX_PUSH_STOCKS": ParamSpec("MAX_PUSH_STOCKS", "max_push_stocks", "int", 1, 5, 1),
    "SENTIMENT_LOW_COVERAGE_COUNT": ParamSpec(
        "SENTIMENT_LOW_COVERAGE_COUNT", "sentiment_low_coverage_count", "int", 5, 30, 2
    ),
    "SENTIMENT_FULL_COVERAGE_COUNT": ParamSpec(
        "SENTIMENT_FULL_COVERAGE_COUNT", "sentiment_full_coverage_count", "int", 30, 300, 10
    ),
}


def get_strategy_param_snapshot() -> dict[str, Any]:
    document = _load_or_initialize_document()
    return {
        "version": int(document.get("version") or 1),
        "updated_at": document.get("updated_at"),
        "source": document.get("source"),
        "last_self_learning_at": document.get("last_self_learning_at"),
        "params": get_strategy_params(),
    }


def get_strategy_params() -> dict[str, Any]:
    document = _load_or_initialize_document()
    raw_params = document.get("params") if isinstance(document.get("params"), dict) else {}
    defaults = default_strategy_params()
    params = {}
    for name, default in defaults.items():
        params[name] = _coerce_param(name, raw_params.get(name, default), default)
    return params


def default_strategy_params() -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for name, spec in PARAM_SPECS.items():
        value = getattr(settings, spec.attr)
        defaults[name] = int(value) if spec.kind == "int" else float(value)
    return defaults


def param_float(name: str, default: float) -> float:
    try:
        return float(get_strategy_params().get(name, default))
    except (TypeError, ValueError):
        return float(default)


def param_int(name: str, default: int) -> int:
    try:
        return int(get_strategy_params().get(name, default))
    except (TypeError, ValueError):
        return int(default)


def apply_strategy_param_changes(
    proposed_changes: dict[str, Any],
    reason: str,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document = _load_or_initialize_document()
    current_params = get_strategy_params()
    applied_changes: dict[str, dict[str, Any]] = {}
    new_params = dict(current_params)

    for name, target_value in proposed_changes.items():
        if name not in PARAM_SPECS:
            continue
        current_value = current_params[name]
        target = _coerce_param(name, target_value, current_value)
        bounded = _limit_single_step(name, current_value, target)
        if bounded != current_value:
            new_params[name] = bounded
            applied_changes[name] = {
                "old": current_value,
                "target": target,
                "new": bounded,
            }

    if not applied_changes:
        return {
            "applied": False,
            "version": int(document.get("version") or 1),
            "params": current_params,
            "changes": {},
            "reason": "No parameter passed safety bounds or step limits.",
        }

    updated = {
        "version": int(document.get("version") or 1) + 1,
        "updated_at": _now(),
        "source": "self_learning",
        "reason": reason,
        "last_self_learning_at": _now(),
        "metrics": metrics or {},
        "changes": applied_changes,
        "params": new_params,
        "bounds": _bounds_payload(),
    }
    _write_document(updated)
    return {
        "applied": True,
        "version": updated["version"],
        "params": new_params,
        "changes": applied_changes,
        "reason": reason,
    }


def _load_or_initialize_document() -> dict[str, Any]:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    if not PARAMS_FILE.exists():
        document = _initial_document()
        _write_document(document)
        return document
    try:
        with PARAMS_FILE.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
        return document if isinstance(document, dict) else _initial_document()
    except (OSError, json.JSONDecodeError):
        return _initial_document()


def _initial_document() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": _now(),
        "source": "env_defaults",
        "reason": "Initial strategy parameters copied from environment defaults.",
        "last_self_learning_at": None,
        "params": default_strategy_params(),
        "bounds": _bounds_payload(),
    }


def _write_document(document: dict[str, Any]) -> None:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(f"{PARAMS_FILE}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, PARAMS_FILE)


def _coerce_param(name: str, value: Any, default: Any) -> Any:
    spec = PARAM_SPECS[name]
    try:
        numeric = int(value) if spec.kind == "int" else float(value)
    except (TypeError, ValueError):
        numeric = default
    numeric = max(spec.minimum, min(spec.maximum, numeric))
    if spec.kind == "int":
        return int(round(numeric))
    return round(float(numeric), 6)


def _limit_single_step(name: str, current_value: Any, target_value: Any) -> Any:
    spec = PARAM_SPECS[name]
    current = float(current_value)
    target = float(target_value)
    if current == target:
        return current_value

    if spec.kind == "int":
        max_step = spec.max_step
    else:
        configured_step = abs(current) * settings.self_learning_max_step_pct
        max_step = min(spec.max_step, max(configured_step, spec.max_step * 0.25))
    delta = max(-max_step, min(max_step, target - current))
    limited = current + delta
    return _coerce_param(name, limited, current_value)


def _bounds_payload() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "minimum": spec.minimum,
            "maximum": spec.maximum,
            "max_step": spec.max_step,
            "kind": spec.kind,
        }
        for name, spec in PARAM_SPECS.items()
    }


def _now() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
