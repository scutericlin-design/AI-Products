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
from app.schemas import StrategyConfigIn, StrategyConfigOut, StrategyVersionOut
from app.services.audit import write_audit_log
from app.services.score_refresh import ready_tushare_owner


router = APIRouter(prefix="/api/system-pool", tags=["system-pool"])
ALLOWED_MARKETS = {"main", "chinext", "star", "beijing"}
MODEL_VERSION = "institutional_score_v4_tushare"
FALLBACK_MODEL_VERSION = "institutional_score_v3"
TUSHARE_READY_STATUSES = {"available", "configured_manual_check"}


def read_pool() -> list[dict[str, str]]:
    if not settings.recommended_pool_path.exists():
        return []
    with settings.recommended_pool_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def get_or_create_config(user: User, db: Session) -> StrategyConfig:
    config = db.scalar(select(StrategyConfig).where(StrategyConfig.user_id == user.id))
    if config is None:
        config = StrategyConfig(user_id=user.id, model_version=MODEL_VERSION)
        db.add(config)
        db.commit()
        db.refresh(config)
    elif config.model_version in {FALLBACK_MODEL_VERSION, "quality_first_v2"}:
        config.model_version = MODEL_VERSION
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


def build_rebuild_command(config: StrategyConfig, user: User, db: Session) -> tuple[list[str], str, str]:
    credential_owner = ready_tushare_owner(user, db)
    if credential_owner is not None:
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "build_tushare_institutional_pool.py"),
            "--email",
            credential_owner.email,
            "--limit",
            str(config.pool_limit),
            "--lookback-trade-days",
            "90",
            "--finance-limit",
            "800",
            "--min-amount-yi",
            str(config.min_amount_yi),
            "--buy-score-threshold",
            str(config.buy_score_threshold),
            "--min-pct-change",
            str(config.min_pct_change),
            "--max-pct-change",
            str(config.max_pct_change),
            "--min-close-position-pct",
            str(config.min_close_position_pct),
            "--max-amplitude-pct",
            str(config.max_amplitude_pct),
            "--target-weight",
            str(config.target_weight),
            "--markets",
            config.market_scope or "main,chinext,star",
            "--sleep",
            "0.03",
        ]
        return command, "tushare_pro", MODEL_VERSION

    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "build_a_share_spot_pool.py"),
        "--limit",
        str(config.pool_limit),
        "--min-amount",
        str(config.min_amount_yi * 100_000_000),
        "--buy-score-threshold",
        str(config.buy_score_threshold),
        "--min-pct-change",
        str(config.min_pct_change),
        "--max-pct-change",
        str(config.max_pct_change),
        "--min-close-position-pct",
        str(config.min_close_position_pct),
        "--max-amplitude-pct",
        str(config.max_amplitude_pct),
        "--target-weight",
        str(config.target_weight),
        "--markets",
        config.market_scope or "main,chinext,star",
    ]
    if "beijing" in (config.market_scope or "").split(","):
        command.append("--include-beijing")
    return command, "akshare_fallback", FALLBACK_MODEL_VERSION


def serialize_config(config: StrategyConfig) -> dict:
    markets = [item for item in (config.market_scope or "main,chinext,star").split(",") if item]
    return {
        "model_version": config.model_version,
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


def create_version_snapshot(config: StrategyConfig, db: Session, note: str | None = None) -> StrategyVersion:
    params = serialize_config(config)
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
    billing_enabled = getattr(user, "_billing_enabled", False)
    if billing_enabled and getattr(user, "role", "customer") != "admin" and getattr(user, "plan", "free") != "pro":
        rows = rows[:10]
    return {"rows": rows}


@router.get("/config", response_model=StrategyConfigOut)
def read_config(user: User = Depends(require_feature("system_pool")), db: Session = Depends(get_db)):
    return serialize_config(get_or_create_config(user, db))


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
    ]:
        if field in params:
            setattr(config, field, params[field])
    markets = [market for market in params.get("markets", []) if market in ALLOWED_MARKETS]
    config.market_scope = ",".join(markets or ["main", "chinext", "star"])
    config.include_beijing = 1 if "beijing" in config.market_scope.split(",") else 0
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
    command, engine, model_version = build_rebuild_command(config, user, db)
    completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=900)
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
    if config.model_version != model_version:
        config.model_version = model_version
        db.commit()
        db.refresh(config)
    write_audit_log(
        db,
        user,
        "system_pool.rebuild",
        config.model_version,
        {"rows": len(read_pool()), "engine": engine, "model_version": model_version, "config": serialize_config(config)},
    )
    return {
        "ok": True,
        "rows": len(read_pool()),
        "engine": engine,
        "model_version": model_version,
        "config": serialize_config(config),
        "stdout": completed.stdout[-4000:],
    }
