from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.database import get_db
from app.deps import current_user
from app.models import AuditLog, DataSourceConfig, PortfolioPosition, User
from app.schemas import DataSourceConfigIn
from app.security import decrypt_secret, encrypt_secret, mask_secret
from app.services.audit import parse_audit_detail, write_audit_log
from app.services.portfolio_advice import build_user_advice
from app.services.timezone import beijing_iso


router = APIRouter(prefix="/api/institutional", tags=["institutional"])

PROVIDERS = {
    "akshare": {
        "label": "AKShare",
        "tier": "open_source",
        "needs_token": False,
        "package": "akshare",
        "role": "实时行情、快速验证、辅助数据",
    },
    "tushare": {
        "label": "TuShare Pro",
        "tier": "paid_api",
        "needs_token": True,
        "package": "tushare",
        "role": "全A历史行情、财务、复权、基础资料",
    },
    "joinquant": {
        "label": "聚宽",
        "tier": "paid_platform",
        "needs_token": True,
        "package": None,
        "role": "历史数据、研究环境、因子/回测补充",
    },
    "ricequant": {
        "label": "米筐",
        "tier": "paid_platform",
        "needs_token": True,
        "package": None,
        "role": "机构级数据、因子、组合优化和回测组件",
    },
    "wind_choice": {
        "label": "Wind / Choice",
        "tier": "terminal",
        "needs_token": True,
        "package": None,
        "role": "生产级行情、财务、公告和一致预期校验",
    },
}

DATA_HEALTH_PATH = PROJECT_ROOT / "data" / "processed" / "data_health_latest.json"
PORTFOLIO_BACKTEST_JSON_PATH = PROJECT_ROOT / "data" / "processed" / "portfolio_backtest_latest.json"
WALK_FORWARD_JSON_PATH = PROJECT_ROOT / "data" / "processed" / "walk_forward_latest.json"
FACTOR_DIAGNOSTICS_PATH = PROJECT_ROOT / "data" / "processed" / "factor_diagnostics_latest.json"
TUSHARE_READY_STATUSES = {"available", "configured_manual_check"}


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def package_available(package_name: str | None) -> bool | None:
    if not package_name:
        return None
    return importlib.util.find_spec(package_name) is not None


def get_platform_tushare_config(db: Session) -> DataSourceConfig | None:
    return db.scalar(
        select(DataSourceConfig)
        .where(
            DataSourceConfig.provider == "tushare",
            DataSourceConfig.status.in_(TUSHARE_READY_STATUSES),
            DataSourceConfig.api_token_cipher.is_not(None),
        )
        .order_by(DataSourceConfig.priority.asc(), DataSourceConfig.user_id.asc())
    )


def validate_tushare_token(token: str, base_url: str | None = None) -> tuple[str, str]:
    if base_url:
        try:
            response = requests.post(
                base_url.rstrip("/"),
                json={
                    "api_name": "daily",
                    "token": token,
                    "params": {
                        "ts_code": "000001.SZ",
                        "start_date": "20260101",
                        "end_date": "20260110",
                    },
                    "fields": "",
                },
                timeout=20,
            )
            payload = response.json()
        except Exception as exc:
            return "connection_failed", f"TuShare 代理连通失败：{exc}"
        code = payload.get("code")
        message = payload.get("msg") or ""
        items = ((payload.get("data") or {}).get("items") or []) if isinstance(payload.get("data"), dict) else []
        if code not in {0, "0"}:
            if "token" in str(message).lower() or "token" in str(message):
                return "invalid_token", f"TuShare 代理返回 token 无效：{message}"
            return "connection_failed", f"TuShare 代理返回错误：{message or code}"
        if not items:
            return "empty_response", "TuShare 代理已连接，但测试接口返回为空。"
        return "available", f"TuShare 代理已连接，测试 daily 返回 {len(items)} 条记录。"

    try:
        import tushare as ts

        ts.set_token(token)
        pro = ts.pro_api()
        if base_url:
            pro._DataApi__http_url = base_url.rstrip("/")
        frame = pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,area,industry,list_date",
        )
    except Exception as exc:  # TuShare raises plain Exception with provider message.
        message = str(exc)
        if "token" in message.lower() or "token" in message:
            return "invalid_token", "TuShare Pro 返回 token 无效，请在 TuShare Pro 个人中心重新复制。"
        return "connection_failed", f"TuShare Pro 连通失败：{message}"
    rows = len(frame) if frame is not None else 0
    if rows <= 0:
        return "empty_response", "TuShare Pro 已连接，但 stock_basic 返回为空。"
    return "available", f"TuShare Pro 已连接，stock_basic 返回 {rows} 条基础资料。"


def serialize_source(config: DataSourceConfig | None, provider: str) -> dict[str, Any]:
    meta = PROVIDERS[provider]
    configured_token = bool(config and config.api_token_cipher)
    package_state = package_available(meta["package"])
    if config:
        status = config.status
    elif provider == "akshare" and package_state:
        status = "available"
    elif package_state is False:
        status = "missing_package"
    else:
        status = "not_configured"
    return {
        "id": config.id if config else None,
        "provider": provider,
        "label": meta["label"],
        "tier": meta["tier"],
        "role": meta["role"],
        "needs_token": meta["needs_token"],
        "package_available": package_state,
        "configured": provider == "akshare" or configured_token,
        "status": status,
        "priority": config.priority if config else 100,
        "token_mask": config.token_mask if config else None,
        "base_url": config.base_url if config else None,
        "notes": config.notes if config else None,
        "last_checked_at": beijing_iso(config.last_checked_at) if config else None,
        "updated_at": beijing_iso(config.updated_at) if config else None,
    }


def serialize_source_with_platform(
    config: DataSourceConfig | None,
    provider: str,
    platform_config: DataSourceConfig | None,
) -> dict[str, Any]:
    result = serialize_source(config, provider)
    if (
        provider == "tushare"
        and platform_config is not None
        and not (config and config.api_token_cipher)
    ):
        result.update(
            {
                "status": platform_config.status,
                "configured": True,
                "token_mask": "平台统一配置",
                "base_url": platform_config.base_url,
                "priority": platform_config.priority,
                "notes": "当前账号使用平台级 TuShare Pro 数据源，无需单独配置 Token。",
                "last_checked_at": beijing_iso(platform_config.last_checked_at),
                "updated_at": beijing_iso(platform_config.updated_at),
                "using_platform_credential": True,
            }
        )
    else:
        result["using_platform_credential"] = False
    return result


def classify_board(symbol: str) -> str:
    code = str(symbol).zfill(6)
    if code.startswith(("4", "8", "9")):
        return "北交所"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("688", "689")):
        return "科创板"
    return "主板"


@router.get("/data-sources")
def list_data_sources(user: User = Depends(current_user), db: Session = Depends(get_db)):
    configs = {
        item.provider: item
        for item in db.scalars(select(DataSourceConfig).where(DataSourceConfig.user_id == user.id)).all()
    }
    platform_tushare_config = get_platform_tushare_config(db)
    return {
        "providers": [
            serialize_source_with_platform(configs.get(provider), provider, platform_tushare_config)
            for provider in PROVIDERS
        ]
    }


@router.put("/data-sources")
def save_data_source(payload: DataSourceConfigIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    provider = payload.provider.strip().lower()
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail="暂不支持该数据源")
    config = db.scalar(
        select(DataSourceConfig).where(DataSourceConfig.user_id == user.id, DataSourceConfig.provider == provider)
    )
    if config is None:
        config = DataSourceConfig(user_id=user.id, provider=provider)
        db.add(config)
    if payload.api_token is not None:
        token = payload.api_token.strip()
        config.api_token_cipher = encrypt_secret(token) if token else None
        config.token_mask = mask_secret(token) if token else None
    config.base_url = payload.base_url
    config.priority = payload.priority
    config.notes = payload.notes
    config.status = "configured" if provider == "akshare" or config.api_token_cipher else "not_configured"
    db.commit()
    db.refresh(config)
    write_audit_log(
        db,
        user,
        "data_source.save",
        provider,
        {"provider": provider, "status": config.status, "priority": config.priority},
    )
    return serialize_source(config, provider)


@router.post("/data-sources/{provider}/check")
def check_data_source(provider: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    normalized = provider.strip().lower()
    if normalized not in PROVIDERS:
        raise HTTPException(status_code=400, detail="暂不支持该数据源")
    meta = PROVIDERS[normalized]
    config = db.scalar(
        select(DataSourceConfig).where(DataSourceConfig.user_id == user.id, DataSourceConfig.provider == normalized)
    )
    package_state = package_available(meta["package"])
    token = decrypt_secret(config.api_token_cipher) if config else None
    base_url = config.base_url if config else None
    platform_tushare_config = get_platform_tushare_config(db) if normalized == "tushare" else None
    using_platform_credential = False
    if normalized == "tushare" and not token and platform_tushare_config is not None:
        token = decrypt_secret(platform_tushare_config.api_token_cipher)
        base_url = platform_tushare_config.base_url
        using_platform_credential = True
    if package_state is False:
        status = "missing_package"
        message = f"{meta['label']} SDK 尚未安装。"
    elif meta["needs_token"] and not token:
        status = "missing_token"
        message = f"{meta['label']} 需要在网页配置 API Token。"
    elif normalized == "tushare":
        status, message = validate_tushare_token(token, base_url)
        if using_platform_credential:
            message = f"平台级 TuShare Pro 数据源可用；{message}"
    elif meta["package"] is None and meta["needs_token"]:
        status = "configured_manual_check"
        message = f"{meta['label']} 已保存凭证信息；正式连通性需要供应商 SDK 或终端环境。"
    else:
        status = "available"
        message = f"{meta['label']} 本地环境可用。"
    checked_at = datetime.utcnow()
    if using_platform_credential and platform_tushare_config is not None:
        platform_tushare_config.last_checked_at = checked_at
        db.commit()
        db.refresh(platform_tushare_config)
    if config is None and not using_platform_credential:
        config = DataSourceConfig(user_id=user.id, provider=normalized)
        db.add(config)
    if config is not None and not using_platform_credential:
        config.status = status
        config.last_checked_at = checked_at
        db.commit()
        db.refresh(config)
    write_audit_log(db, user, "data_source.check", normalized, {"status": status, "message": message})
    result = serialize_source_with_platform(config, normalized, platform_tushare_config)
    if using_platform_credential:
        result["status"] = status
    result["message"] = message
    return result


@router.get("/portfolio-risk")
def portfolio_risk_review(user: User = Depends(current_user), db: Session = Depends(get_db)):
    positions = db.scalars(
        select(PortfolioPosition).where(PortfolioPosition.user_id == user.id).order_by(PortfolioPosition.weight.desc())
    ).all()
    advice = build_user_advice(positions)
    total_weight = sum(float(row.get("weight") or 0) for row in advice)
    max_single = max((float(row.get("weight") or 0) for row in advice), default=0.0)
    board_exposure: dict[str, float] = {}
    action_exposure: dict[str, float] = {}
    low_score_weight = 0.0
    unscored_weight = 0.0
    reduce_weight = 0.0
    reduce_actions = {"reduce", "trim_to_risk_budget", "reduce_light", "large_reduce", "strategy_clear", "hard_exit", "exit_or_strong_reduce"}
    hhi = 0.0
    for row in advice:
        weight = float(row.get("weight") or 0)
        hhi += weight * weight
        board = classify_board(str(row.get("symbol") or ""))
        board_exposure[board] = board_exposure.get(board, 0.0) + weight
        action = str(row.get("portfolio_action") or "unknown")
        action_exposure[action] = action_exposure.get(action, 0.0) + weight
        score = row.get("holding_score") if row.get("holding_score") is not None else row.get("price_factor_score")
        if score is None:
            unscored_weight += weight
        elif float(score) < 50:
            low_score_weight += weight
        if action in reduce_actions:
            reduce_weight += weight

    high_beta_board_weight = board_exposure.get("创业板", 0.0) + board_exposure.get("科创板", 0.0)
    breaches: list[dict[str, str]] = []
    if total_weight > 0.90:
        breaches.append({"level": "high", "item": "总仓位", "message": "总仓位超过 90%，缺少现金缓冲。"})
    if total_weight > 0 and 1 - total_weight < 0.10:
        breaches.append({"level": "medium", "item": "现金底仓", "message": "现金底仓低于 10%。"})
    if max_single > 0.12:
        breaches.append({"level": "high", "item": "单票集中度", "message": "存在单票仓位超过 12%。"})
    if high_beta_board_weight > 0.55:
        breaches.append({"level": "medium", "item": "高弹性板块", "message": "创业板/科创板暴露超过 55%。"})
    if low_score_weight > 0.20:
        breaches.append({"level": "medium", "item": "低评分持仓", "message": "评分低于 50 的持仓权重超过 20%。"})
    if unscored_weight > 0.10:
        breaches.append({"level": "medium", "item": "未评分持仓", "message": "未评分持仓权重超过 10%，需要补行情或排查代码。"})
    if reduce_weight > 0.20:
        breaches.append({"level": "high", "item": "降风险建议", "message": "建议降仓/退出的持仓权重超过 20%。"})

    risk_level = "high" if any(item["level"] == "high" for item in breaches) else "medium" if breaches else "controlled"
    return {
        "risk_level": risk_level,
        "position_count": len(advice),
        "total_weight": total_weight,
        "cash_weight": max(0.0, 1 - total_weight),
        "max_single_weight": max_single,
        "concentration_hhi": hhi,
        "high_beta_board_weight": high_beta_board_weight,
        "low_score_weight": low_score_weight,
        "unscored_weight": unscored_weight,
        "reduce_weight": reduce_weight,
        "board_exposure": board_exposure,
        "action_exposure": action_exposure,
        "breaches": breaches,
        "notes": [
            "组合风险暴露来自当前账户持仓和最新评分，不代表自动交易指令。",
            "机构化风控下一步应接入行业、Beta、市值、相关性和 VaR/ES。",
        ],
    }


@router.get("/model-governance")
def model_governance(user: User = Depends(current_user), db: Session = Depends(get_db)):
    data_health = read_json(DATA_HEALTH_PATH, {"status": "missing", "datasets": []})
    backtest = read_json(PORTFOLIO_BACKTEST_JSON_PATH, {"metrics": {}})
    walk_forward = read_json(WALK_FORWARD_JSON_PATH, {"summary": {}})
    factors = read_json(FACTOR_DIAGNOSTICS_PATH, {"factor_summaries": []})
    sources = list_data_sources(user, db)["providers"]
    metrics = backtest.get("metrics") or {}
    summary = walk_forward.get("summary") or {}
    price_factor = next((item for item in factors.get("factor_summaries", []) if item.get("factor") == "price_factor_score"), {})
    gates = [
        {
            "name": "数据覆盖",
            "status": "pass" if metrics.get("tradable_symbol_count", 0) >= 100 else "fail",
            "detail": f"可回测股票数 {metrics.get('tradable_symbol_count', 0)}，机构最低建议 100+。",
        },
        {
            "name": "组合回测有效性",
            "status": "pass" if metrics.get("research_ready") else "fail",
            "detail": metrics.get("data_quality_note") or "缺少组合回测结果。",
        },
        {
            "name": "样本外稳定性",
            "status": "pass" if summary.get("production_ready") else "fail",
            "detail": summary.get("stability_reason") or "缺少 walk-forward 结果。",
        },
        {
            "name": "因子有效性",
            "status": "pass" if float(price_factor.get("rank_ic_mean") or 0) >= 0.04 else "warn",
            "detail": f"price_factor_score RankIC={float(price_factor.get('rank_ic_mean') or 0):.3f}。",
        },
        {
            "name": "商业数据源",
            "status": "pass"
            if any(
                item["provider"] != "akshare" and item["status"] in {"available", "configured_manual_check"}
                for item in sources
            )
            else "warn",
            "detail": "生产级建议至少配置 TuShare Pro/米筐/聚宽/Wind/Choice 之一。",
        },
        {
            "name": "人工复核",
            "status": "pass",
            "detail": "当前系统保留人工确认，不自动下单。",
        },
    ]
    pass_count = sum(1 for gate in gates if gate["status"] == "pass")
    fail_count = sum(1 for gate in gates if gate["status"] == "fail")
    readiness_score = round(pass_count / len(gates) * 100)
    readiness = "research" if fail_count else "production_watch" if readiness_score < 90 else "production_candidate"
    return {
        "model_version": "institutional_score_v7_profile_adaptive_tushare",
        "readiness": readiness,
        "readiness_score": readiness_score,
        "gates": gates,
        "required_next_steps": [
            "补齐全A历史数据和财务/行业/复权字段。",
            "用 5-10 年全市场样本重跑因子检验、组合回测和样本外验证。",
            "把交易执行、风控阈值、模型审批和参数变更纳入审计流程。",
        ],
        "reference_controls": [
            "数据质量与偏差控制",
            "算法开发、测试和持续监控",
            "可解释性和用户披露",
            "第三方数据供应商管理",
            "人工复核和职责边界",
        ],
    }


@router.get("/audit-logs")
def audit_logs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(AuditLog).where(AuditLog.user_id == user.id).order_by(AuditLog.created_at.desc()).limit(50)
    ).all()
    return {
        "rows": [
            {
                "id": row.id,
                "action": row.action,
                "target": row.target,
                "detail": parse_audit_detail(row),
                "created_at": beijing_iso(row.created_at),
            }
            for row in rows
        ]
    }
