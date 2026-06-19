from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, settings
from app.database import get_db
from app.deps import require_feature
from app.models import DataSourceConfig, StrategyConfig, StrategyVersion, User
from app.schemas import StrategyConfigIn, StrategyConfigOut, StrategyProfileIn, StrategyVersionOut
from app.services.audit import write_audit_log
from app.services.score_refresh import ready_tushare_owner


router = APIRouter(prefix="/api/system-pool", tags=["system-pool"])
ALLOWED_MARKETS = {"main", "chinext", "star", "beijing"}
DEFAULT_STRATEGY_TYPE = "short_elastic_2_8w"
STRATEGY_MODEL_VERSIONS = {
    "short_elastic_2_8w": "institutional_score_v6_short_adaptive_tushare",
    "mid_long_quality_3_12m": "institutional_score_v6_midlong_adaptive_tushare",
}
MODEL_VERSION = STRATEGY_MODEL_VERSIONS[DEFAULT_STRATEGY_TYPE]
STRATEGY_REBUILD_PRESETS = {
    "short_elastic_2_8w": {"lookback_trade_days": "90", "finance_limit": "800"},
    "mid_long_quality_3_12m": {"lookback_trade_days": "180", "finance_limit": "800"},
}
FALLBACK_MODEL_VERSION = "institutional_score_v3"
TUSHARE_READY_STATUSES = {"available", "configured_manual_check"}
STRATEGY_POOL_FILES = {
    "short": {
        "strategy_type": "short_elastic_2_8w",
        "label": "短期选股 2-8周",
        "path": PROJECT_ROOT / "data" / "processed" / "recommended_pool_short.csv",
    },
    "mid_long": {
        "strategy_type": "mid_long_quality_3_12m",
        "label": "长期选股 3-12月",
        "path": PROJECT_ROOT / "data" / "processed" / "recommended_pool_midlong.csv",
    },
}
STRATEGY_PROFILE_DEFAULTS = {
    "short": {
        "strategy_type": "short_elastic_2_8w",
        "model_version": STRATEGY_MODEL_VERSIONS["short_elastic_2_8w"],
        "label": "短期选股",
        "pool_limit": 30,
        "min_amount_yi": 3.0,
        "buy_score_threshold": 78.0,
        "min_pct_change": 1.0,
        "max_pct_change": 9.7,
        "min_close_position_pct": 55.0,
        "max_amplitude_pct": 12.0,
        "target_weight": 0.05,
        "markets": ["main", "chinext", "star"],
        "include_beijing": False,
    },
    "mid_long": {
        "strategy_type": "mid_long_quality_3_12m",
        "model_version": STRATEGY_MODEL_VERSIONS["mid_long_quality_3_12m"],
        "label": "长期选股",
        "pool_limit": 30,
        "min_amount_yi": 3.0,
        "buy_score_threshold": 76.0,
        "min_pct_change": -3.0,
        "max_pct_change": 9.7,
        "min_close_position_pct": 45.0,
        "max_amplitude_pct": 12.0,
        "target_weight": 0.06,
        "markets": ["main", "chinext", "star"],
        "include_beijing": False,
    },
}
STRATEGY_TYPE_TO_KEY = {
    "short_elastic_2_8w": "short",
    "mid_long_quality_3_12m": "mid_long",
}


def read_pool_path(path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_pool() -> list[dict[str, str]]:
    return read_pool_path(settings.recommended_pool_path)


def pool_meta(path, rows: list[dict[str, str]], strategy_key: str | None = None) -> dict:
    updated_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat() if path.exists() else None
    return {
        "strategy_key": strategy_key,
        "updated_at": updated_at,
        "pool_strategy_type": rows[0].get("strategy_type") if rows else None,
        "pool_model_version": rows[0].get("model_version") if rows else None,
        "rows": rows,
    }


def legacy_config_profile(config: StrategyConfig) -> dict:
    markets = [item for item in (config.market_scope or "main,chinext,star").split(",") if item]
    return {
        "pool_limit": config.pool_limit,
        "min_amount_yi": config.min_amount_yi,
        "buy_score_threshold": config.buy_score_threshold,
        "min_pct_change": config.min_pct_change,
        "max_pct_change": config.max_pct_change,
        "min_close_position_pct": config.min_close_position_pct,
        "max_amplitude_pct": config.max_amplitude_pct,
        "target_weight": config.target_weight,
        "markets": markets or ["main", "chinext", "star"],
        "include_beijing": bool(config.include_beijing),
    }


def normalize_profile(strategy_key: str, profile: dict | None) -> dict:
    defaults = STRATEGY_PROFILE_DEFAULTS[strategy_key]
    profile = profile or {}
    markets = [market for market in profile.get("markets", defaults["markets"]) if market in ALLOWED_MARKETS]
    if not markets:
        markets = list(defaults["markets"])
    normalized = {
        key: profile.get(key, defaults[key])
        for key in [
            "pool_limit",
            "min_amount_yi",
            "buy_score_threshold",
            "min_pct_change",
            "max_pct_change",
            "min_close_position_pct",
            "max_amplitude_pct",
            "target_weight",
        ]
    }
    normalized["pool_limit"] = int(normalized["pool_limit"])
    for key in [
        "min_amount_yi",
        "buy_score_threshold",
        "min_pct_change",
        "max_pct_change",
        "min_close_position_pct",
        "max_amplitude_pct",
        "target_weight",
    ]:
        normalized[key] = float(normalized[key])
    normalized["markets"] = markets
    normalized["include_beijing"] = bool(profile.get("include_beijing", "beijing" in markets))
    normalized["strategy_type"] = defaults["strategy_type"]
    normalized["model_version"] = defaults["model_version"]
    normalized["label"] = defaults["label"]
    preset = STRATEGY_REBUILD_PRESETS[defaults["strategy_type"]]
    normalized["lookback_trade_days"] = int(profile.get("lookback_trade_days", preset["lookback_trade_days"]))
    normalized["finance_limit"] = int(profile.get("finance_limit", preset["finance_limit"]))
    return normalized


def read_strategy_profiles(config: StrategyConfig) -> dict[str, dict]:
    raw: dict = {}
    if getattr(config, "strategy_profiles_json", None):
        try:
            raw = json.loads(config.strategy_profiles_json or "{}")
        except json.JSONDecodeError:
            raw = {}
    legacy_key = STRATEGY_TYPE_TO_KEY.get(getattr(config, "strategy_type", DEFAULT_STRATEGY_TYPE), "short")
    if legacy_key not in raw:
        raw[legacy_key] = legacy_config_profile(config)
    return {
        "short": normalize_profile("short", raw.get("short")),
        "mid_long": normalize_profile("mid_long", raw.get("mid_long")),
    }


def write_strategy_profiles(config: StrategyConfig, profiles: dict[str, dict]) -> None:
    compact = {}
    for strategy_key in ["short", "mid_long"]:
        profile = normalize_profile(strategy_key, profiles.get(strategy_key))
        compact[strategy_key] = {
            key: profile[key]
            for key in [
                "pool_limit",
                "min_amount_yi",
                "buy_score_threshold",
                "min_pct_change",
                "max_pct_change",
                "min_close_position_pct",
                "max_amplitude_pct",
                "target_weight",
                "markets",
                "include_beijing",
                "lookback_trade_days",
                "finance_limit",
            ]
        }
    config.strategy_profiles_json = json.dumps(compact, ensure_ascii=False, sort_keys=True)


def get_or_create_config(user: User, db: Session) -> StrategyConfig:
    config = db.scalar(select(StrategyConfig).where(StrategyConfig.user_id == user.id))
    if config is None:
        config = StrategyConfig(
            user_id=user.id,
            strategy_type=DEFAULT_STRATEGY_TYPE,
            model_version=STRATEGY_MODEL_VERSIONS[DEFAULT_STRATEGY_TYPE],
        )
        write_strategy_profiles(config, STRATEGY_PROFILE_DEFAULTS)
        db.add(config)
        db.commit()
        db.refresh(config)
    else:
        strategy_type = getattr(config, "strategy_type", None) or DEFAULT_STRATEGY_TYPE
        desired_model_version = STRATEGY_MODEL_VERSIONS.get(
            strategy_type,
            STRATEGY_MODEL_VERSIONS[DEFAULT_STRATEGY_TYPE],
        )
        config.strategy_type = strategy_type
        if config.model_version in {FALLBACK_MODEL_VERSION, "quality_first_v2", "institutional_score_v4_tushare"}:
            config.model_version = desired_model_version
        elif config.model_version != desired_model_version and strategy_type in STRATEGY_MODEL_VERSIONS:
            config.model_version = desired_model_version
        if not getattr(config, "strategy_profiles_json", None):
            profiles = read_strategy_profiles(config)
            write_strategy_profiles(config, profiles)
        if db.is_modified(config):
            db.commit()
            db.refresh(config)
    return config


def get_ready_tushare_config(user: User, db: Session) -> DataSourceConfig | None:
    return db.scalar(
        select(DataSourceConfig).where(
            DataSourceConfig.user_id == user.id,
            DataSourceConfig.provider == "tushare",
            DataSourceConfig.status.in_(TUSHARE_READY_STATUSES),
            DataSourceConfig.api_token_cipher.is_not(None),
        )
    )


def build_rebuild_command(
    config: StrategyConfig,
    user: User,
    db: Session,
    strategy_type_override: str | None = None,
) -> tuple[list[str], str, str]:
    credential_owner = ready_tushare_owner(user, db)
    strategy_type = strategy_type_override or getattr(config, "strategy_type", None) or DEFAULT_STRATEGY_TYPE
    model_version = STRATEGY_MODEL_VERSIONS.get(strategy_type, STRATEGY_MODEL_VERSIONS[DEFAULT_STRATEGY_TYPE])
    strategy_key = STRATEGY_TYPE_TO_KEY.get(strategy_type, "short")
    profile = read_strategy_profiles(config)[strategy_key]
    market_scope = ",".join(profile["markets"])
    if credential_owner is not None:
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "build_tushare_institutional_pool.py"),
            "--email",
            credential_owner.email,
            "--strategy-type",
            strategy_type,
            "--limit",
            str(profile["pool_limit"]),
            "--lookback-trade-days",
            str(profile["lookback_trade_days"]),
            "--finance-limit",
            str(profile["finance_limit"]),
            "--min-amount-yi",
            str(profile["min_amount_yi"]),
            "--buy-score-threshold",
            str(profile["buy_score_threshold"]),
            "--min-pct-change",
            str(profile["min_pct_change"]),
            "--max-pct-change",
            str(profile["max_pct_change"]),
            "--min-close-position-pct",
            str(profile["min_close_position_pct"]),
            "--max-amplitude-pct",
            str(profile["max_amplitude_pct"]),
            "--target-weight",
            str(profile["target_weight"]),
            "--markets",
            market_scope,
            "--sleep",
            "0.03",
        ]
        return command, "tushare_pro", model_version

    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "build_a_share_spot_pool.py"),
        "--limit",
        str(profile["pool_limit"]),
        "--min-amount",
        str(profile["min_amount_yi"] * 100_000_000),
        "--buy-score-threshold",
        str(profile["buy_score_threshold"]),
        "--min-pct-change",
        str(profile["min_pct_change"]),
        "--max-pct-change",
        str(profile["max_pct_change"]),
        "--min-close-position-pct",
        str(profile["min_close_position_pct"]),
        "--max-amplitude-pct",
        str(profile["max_amplitude_pct"]),
        "--target-weight",
        str(profile["target_weight"]),
        "--markets",
        market_scope,
    ]
    if "beijing" in profile["markets"]:
        command.append("--include-beijing")
    return command, "akshare_fallback", FALLBACK_MODEL_VERSION


def tail_output(value: object, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[-limit:]
    return str(value)[-limit:]


def run_rebuild_command(command: list[str], engine: str, strategy_type: str, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504,
            detail={
                "message": f"{strategy_type} rebuild timed out after {timeout} seconds",
                "engine": engine,
                "stdout": tail_output(exc.stdout),
                "stderr": tail_output(exc.stderr),
            },
        ) from exc


def build_dual_rebuild_command(config: StrategyConfig, user: User, db: Session) -> tuple[list[str], str, str]:
    credential_owner = ready_tushare_owner(user, db)
    if credential_owner is None:
        command, engine, model_version = build_rebuild_command(config, user, db, strategy_type_override=DEFAULT_STRATEGY_TYPE)
        return command, engine, model_version

    profiles = read_strategy_profiles(config)
    script_profiles = {
        profile["strategy_type"]: {
            key: profile[key]
            for key in [
                "pool_limit",
                "min_amount_yi",
                "buy_score_threshold",
                "min_pct_change",
                "max_pct_change",
                "min_close_position_pct",
                "max_amplitude_pct",
                "target_weight",
                "markets",
                "include_beijing",
                "lookback_trade_days",
                "finance_limit",
            ]
        }
        for profile in profiles.values()
    }
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "build_tushare_institutional_pool.py"),
        "--email",
        credential_owner.email,
        "--strategy-type",
        "all",
        "--primary-strategy-type",
        DEFAULT_STRATEGY_TYPE,
        "--strategy-config-json",
        json.dumps(script_profiles, ensure_ascii=False, sort_keys=True),
        "--sleep",
        "0.03",
    ]
    return command, "tushare_pro", "dual_strategy_tushare"


def strategy_pool_counts() -> dict[str, int]:
    return {
        "short": len(read_pool_path(STRATEGY_POOL_FILES["short"]["path"])),
        "mid_long": len(read_pool_path(STRATEGY_POOL_FILES["mid_long"]["path"])),
    }


def serialize_config(config: StrategyConfig) -> dict:
    markets = [item for item in (config.market_scope or "main,chinext,star").split(",") if item]
    strategy_type = getattr(config, "strategy_type", None) or DEFAULT_STRATEGY_TYPE
    return {
        "model_version": config.model_version,
        "strategy_type": strategy_type,
        "pool_limit": config.pool_limit,
        "min_amount_yi": config.min_amount_yi,
        "buy_score_threshold": config.buy_score_threshold,
        "min_pct_change": config.min_pct_change,
        "max_pct_change": config.max_pct_change,
        "min_close_position_pct": config.min_close_position_pct,
        "max_amplitude_pct": config.max_amplitude_pct,
        "target_weight": config.target_weight,
        "markets": markets,
        "include_beijing": bool(config.include_beijing),
    }


def serialize_profile(strategy_key: str, profile: dict) -> dict:
    return {
        key: profile[key]
        for key in [
            "label",
            "strategy_type",
            "model_version",
            "pool_limit",
            "min_amount_yi",
            "buy_score_threshold",
            "min_pct_change",
            "max_pct_change",
            "min_close_position_pct",
            "max_amplitude_pct",
            "target_weight",
            "markets",
            "include_beijing",
            "lookback_trade_days",
            "finance_limit",
        ]
    } | {"strategy_key": strategy_key}


def serialize_profiles(config: StrategyConfig) -> dict:
    profiles = read_strategy_profiles(config)
    return {
        "profiles": {
            "short": serialize_profile("short", profiles["short"]),
            "mid_long": serialize_profile("mid_long", profiles["mid_long"]),
        }
    }


def create_version_snapshot(config: StrategyConfig, db: Session, note: str | None = None) -> StrategyVersion:
    params = serialize_config(config)
    params["profiles"] = serialize_profiles(config)["profiles"]
    version = StrategyVersion(
        user_id=config.user_id,
        version_code=f"{config.model_version}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        model_version=config.model_version,
        params_json=json.dumps(params, ensure_ascii=False, sort_keys=True),
        note=note,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def serialize_version(version: StrategyVersion) -> dict:
    return {
        "id": version.id,
        "version_code": version.version_code,
        "model_version": version.model_version,
        "params": json.loads(version.params_json),
        "note": version.note,
        "created_at": version.created_at.isoformat(),
    }


@router.get("")
def list_system_pool(user: User = Depends(require_feature("system_pool"))):
    rows = read_pool()
    meta = pool_meta(settings.recommended_pool_path, rows)
    billing_enabled = getattr(user, "_billing_enabled", False)
    if billing_enabled and getattr(user, "role", "customer") != "admin" and getattr(user, "plan", "free") != "pro":
        meta["rows"] = meta["rows"][:10]
    return {
        "rows": meta["rows"],
        "updated_at": meta["updated_at"],
        "pool_strategy_type": meta["pool_strategy_type"],
        "pool_model_version": meta["pool_model_version"],
    }


@router.get("/strategies")
def list_strategy_pools(user: User = Depends(require_feature("system_pool"))):
    billing_enabled = getattr(user, "_billing_enabled", False)
    limited = billing_enabled and getattr(user, "role", "customer") != "admin" and getattr(user, "plan", "free") != "pro"
    current_rows = read_pool()
    current_strategy_type = current_rows[0].get("strategy_type") if current_rows else None
    pools = {}
    for strategy_key, config in STRATEGY_POOL_FILES.items():
        path = config["path"]
        rows = read_pool_path(path)
        if not rows and current_strategy_type == config["strategy_type"]:
            path = settings.recommended_pool_path
            rows = current_rows
        if limited:
            rows = rows[:10]
        item = pool_meta(path, rows, strategy_key=strategy_key)
        item["label"] = config["label"]
        item["expected_strategy_type"] = config["strategy_type"]
        pools[strategy_key] = item
    return {"pools": pools}


@router.get("/config", response_model=StrategyConfigOut)
def read_config(user: User = Depends(require_feature("system_pool")), db: Session = Depends(get_db)):
    return serialize_config(get_or_create_config(user, db))


@router.get("/profiles")
def read_profiles(user: User = Depends(require_feature("system_pool")), db: Session = Depends(get_db)):
    return serialize_profiles(get_or_create_config(user, db))


@router.put("/profiles/{strategy_key}")
def save_profile(
    strategy_key: str,
    payload: StrategyProfileIn,
    user: User = Depends(require_feature("system_pool")),
    db: Session = Depends(get_db),
):
    if strategy_key not in STRATEGY_PROFILE_DEFAULTS:
        raise HTTPException(status_code=404, detail="策略配置不存在")
    if payload.max_pct_change <= payload.min_pct_change:
        raise HTTPException(status_code=400, detail="最高涨幅必须大于最低涨幅")
    markets = [market for market in payload.markets if market in ALLOWED_MARKETS]
    if not markets:
        raise HTTPException(status_code=400, detail="至少选择一个市场板块")

    config = get_or_create_config(user, db)
    profiles = read_strategy_profiles(config)
    profile = payload.model_dump()
    profile["markets"] = markets
    profile["include_beijing"] = "beijing" in markets
    profile["lookback_trade_days"] = profiles[strategy_key]["lookback_trade_days"]
    profile["finance_limit"] = profiles[strategy_key]["finance_limit"]
    profiles[strategy_key] = normalize_profile(strategy_key, profile)
    write_strategy_profiles(config, profiles)

    if STRATEGY_TYPE_TO_KEY.get(getattr(config, "strategy_type", DEFAULT_STRATEGY_TYPE), "short") == strategy_key:
        config.pool_limit = profiles[strategy_key]["pool_limit"]
        config.min_amount_yi = profiles[strategy_key]["min_amount_yi"]
        config.buy_score_threshold = profiles[strategy_key]["buy_score_threshold"]
        config.min_pct_change = profiles[strategy_key]["min_pct_change"]
        config.max_pct_change = profiles[strategy_key]["max_pct_change"]
        config.min_close_position_pct = profiles[strategy_key]["min_close_position_pct"]
        config.max_amplitude_pct = profiles[strategy_key]["max_amplitude_pct"]
        config.target_weight = profiles[strategy_key]["target_weight"]
        config.market_scope = ",".join(profiles[strategy_key]["markets"])
        config.include_beijing = 1 if profiles[strategy_key]["include_beijing"] else 0
    db.commit()
    db.refresh(config)
    create_version_snapshot(config, db, note=f"{strategy_key}_profile_save")
    write_audit_log(
        db,
        user,
        "model.profile_save",
        profiles[strategy_key]["model_version"],
        {"strategy_key": strategy_key, "profile": serialize_profile(strategy_key, profiles[strategy_key])},
    )
    return serialize_profiles(config)


@router.put("/config", response_model=StrategyConfigOut)
def save_config(payload: StrategyConfigIn, user: User = Depends(require_feature("system_pool")), db: Session = Depends(get_db)):
    if payload.max_pct_change <= payload.min_pct_change:
        raise HTTPException(status_code=400, detail="最高涨幅必须大于最低涨幅")
    markets = [market for market in payload.markets if market in ALLOWED_MARKETS]
    if not markets:
        raise HTTPException(status_code=400, detail="至少选择一个市场板块")
    config = get_or_create_config(user, db)
    for field, value in payload.model_dump().items():
        if field == "markets":
            config.market_scope = ",".join(markets)
            config.include_beijing = 1 if "beijing" in markets else 0
        elif field == "include_beijing":
            setattr(config, field, 1 if value else 0)
        else:
            setattr(config, field, value)
    config.model_version = STRATEGY_MODEL_VERSIONS.get(
        config.strategy_type,
        STRATEGY_MODEL_VERSIONS[DEFAULT_STRATEGY_TYPE],
    )
    profiles = read_strategy_profiles(config)
    profile_key = STRATEGY_TYPE_TO_KEY.get(config.strategy_type, "short")
    profiles[profile_key] = normalize_profile(profile_key, legacy_config_profile(config))
    write_strategy_profiles(config, profiles)
    db.commit()
    db.refresh(config)
    create_version_snapshot(config, db, note="manual_config_save")
    write_audit_log(db, user, "model.config_save", config.model_version, serialize_config(config))
    return serialize_config(config)


@router.get("/versions", response_model=list[StrategyVersionOut])
def list_versions(user: User = Depends(require_feature("system_pool")), db: Session = Depends(get_db)):
    versions = db.scalars(
        select(StrategyVersion)
        .where(StrategyVersion.user_id == user.id)
        .order_by(StrategyVersion.created_at.desc())
        .limit(30)
    ).all()
    return [serialize_version(version) for version in versions]


@router.post("/versions/{version_id}/restore", response_model=StrategyConfigOut)
def restore_version(version_id: int, user: User = Depends(require_feature("system_pool")), db: Session = Depends(get_db)):
    version = db.scalar(
        select(StrategyVersion).where(StrategyVersion.id == version_id, StrategyVersion.user_id == user.id)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="模型版本不存在")
    params = json.loads(version.params_json)
    config = get_or_create_config(user, db)
    for field in [
        "pool_limit",
        "min_amount_yi",
        "buy_score_threshold",
        "min_pct_change",
        "max_pct_change",
        "min_close_position_pct",
        "max_amplitude_pct",
        "target_weight",
        "strategy_type",
    ]:
        if field in params:
            setattr(config, field, params[field])
    markets = [market for market in params.get("markets", []) if market in ALLOWED_MARKETS]
    config.market_scope = ",".join(markets or ["main", "chinext", "star"])
    config.include_beijing = 1 if "beijing" in config.market_scope.split(",") else 0
    if isinstance(params.get("profiles"), dict):
        write_strategy_profiles(config, params["profiles"])
    config.model_version = STRATEGY_MODEL_VERSIONS.get(
        getattr(config, "strategy_type", DEFAULT_STRATEGY_TYPE),
        STRATEGY_MODEL_VERSIONS[DEFAULT_STRATEGY_TYPE],
    )
    db.commit()
    db.refresh(config)
    create_version_snapshot(config, db, note=f"restore_from_{version.version_code}")
    write_audit_log(
        db,
        user,
        "model.config_restore",
        version.version_code,
        {"version_id": version.id, "version_code": version.version_code},
    )
    return serialize_config(config)


@router.post("/rebuild")
def rebuild_system_pool(user: User = Depends(require_feature("system_pool")), db: Session = Depends(get_db)):
    config = get_or_create_config(user, db)
    command, engine, model_version = build_dual_rebuild_command(config, user, db)
    completed = run_rebuild_command(
        command,
        engine,
        "dual_strategy",
        timeout=1200,
    )
    if completed.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "System pool rebuild failed",
                "engine": engine,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
        )
    desired_model_version = STRATEGY_MODEL_VERSIONS[DEFAULT_STRATEGY_TYPE]
    if config.model_version != desired_model_version:
        config.model_version = desired_model_version
        db.commit()
        db.refresh(config)
    counts = strategy_pool_counts()
    write_audit_log(
        db,
        user,
        "system_pool.rebuild",
        config.model_version,
        {"rows": counts, "engine": engine, "model_version": model_version, "profiles": serialize_profiles(config)},
    )
    return {
        "ok": True,
        "rows": counts["short"] + counts["mid_long"],
        "rows_short": counts["short"],
        "rows_mid_long": counts["mid_long"],
        "engine": engine,
        "model_version": model_version,
        "config": serialize_config(config),
        "profiles": serialize_profiles(config)["profiles"],
        "strategy_results": [
            {"strategy_type": "short_elastic_2_8w", "model_version": STRATEGY_MODEL_VERSIONS["short_elastic_2_8w"], "rows": counts["short"]},
            {"strategy_type": "mid_long_quality_3_12m", "model_version": STRATEGY_MODEL_VERSIONS["mid_long_quality_3_12m"], "rows": counts["mid_long"]},
        ],
        "stdout": completed.stdout[-4000:],
    }


@router.post("/rebuild-dual")
def rebuild_dual_strategy_scores(user: User = Depends(require_feature("system_pool")), db: Session = Depends(get_db)):
    return rebuild_system_pool(user=user, db=db)
