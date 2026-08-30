from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import KnowledgeJob
from app.services.knowledge_orchestrator import (
    json_loads,
    knowledge_help_text,
    orchestrator,
    parse_knowledge_command,
    verify_source_pack_token,
)


router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
feishu_router = APIRouter(prefix="/api/feishu/knowledge", tags=["feishu-knowledge"])


class KnowledgeCommandIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    requester: str | None = Field(default=None, max_length=128)
    chat_id: str | None = Field(default=None, max_length=128)
    source: str = Field(default="api", max_length=32)
    source_event_id: str | None = Field(default=None, max_length=128)
    run_async: bool = True


def require_knowledge_secret(x_knowledge_secret: str | None = Header(default=None)) -> None:
    if settings.knowledge_command_secret and x_knowledge_secret != settings.knowledge_command_secret:
        raise HTTPException(status_code=403, detail="Invalid knowledge command secret")


def verify_feishu_payload(payload: dict[str, Any]) -> None:
    if not settings.feishu_verification_token:
        return
    token = payload.get("token") or (payload.get("header") or {}).get("token")
    if token != settings.feishu_verification_token:
        raise HTTPException(status_code=403, detail="Invalid Feishu verification token")


def verify_feishu_or_shared_secret(payload: dict[str, Any], request: Request, header_secret: str | None) -> None:
    if settings.feishu_verification_token:
        verify_feishu_payload(payload)
        return
    if settings.knowledge_command_secret:
        provided = header_secret or request.query_params.get("secret")
        if provided != settings.knowledge_command_secret:
            raise HTTPException(status_code=403, detail="Missing Feishu verification token or knowledge secret")


def extract_feishu_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return content
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "").strip()
    return ""


def serialize_or_404(job: KnowledgeJob | None) -> dict[str, Any]:
    if job is None:
        raise HTTPException(status_code=404, detail="Knowledge job not found")
    return orchestrator.serialize_job(job)


def latest_job_for_context(db: Session, chat_id: str | None = None, requester: str | None = None) -> KnowledgeJob | None:
    query = select(KnowledgeJob)
    if chat_id:
        query = query.where(KnowledgeJob.chat_id == chat_id)
    elif requester:
        query = query.where(KnowledgeJob.requester == requester)
    query = query.order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id.desc()).limit(1)
    return db.scalar(query)


def job_by_key(db: Session, job_key: str | None) -> KnowledgeJob | None:
    if not job_key:
        return None
    value = job_key.strip()
    if value.isdigit():
        return db.get(KnowledgeJob, int(value))
    return db.scalar(select(KnowledgeJob).where(KnowledgeJob.job_key == value))


def source_pack_path_or_404(job: KnowledgeJob, kind: str) -> Path:
    normalized = kind.lower()
    key_by_kind = {
        "md": "markdown_path",
        "markdown": "markdown_path",
        "txt": "text_path",
        "text": "text_path",
    }
    if normalized not in key_by_kind:
        raise HTTPException(status_code=404, detail="Unsupported source pack type")
    manifest = json_loads(job.source_pack_manifest_json, {}) or {}
    path_value = manifest.get(key_by_kind[normalized])
    if not path_value:
        raise HTTPException(status_code=404, detail="Source pack file not found for this job")
    path = Path(path_value).resolve()
    allowed_roots = [
        settings.knowledge_source_packs_dir.resolve(),
        settings.knowledge_stack_dir.resolve(),
    ]
    if not any(path.is_relative_to(root) for root in allowed_roots):
        raise HTTPException(status_code=403, detail="Source pack path is outside the allowed directory")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Source pack file is missing on disk")
    return path


def authorize_download(request: Request, job: KnowledgeJob, kind: str, header_secret: str | None) -> None:
    if settings.knowledge_command_secret and header_secret == settings.knowledge_command_secret:
        return
    token = request.query_params.get("token") or ""
    normalized = "md" if kind.lower() in {"md", "markdown"} else "txt" if kind.lower() in {"txt", "text"} else kind
    if token and verify_source_pack_token(token, job.job_key, normalized):
        return
    if not settings.knowledge_command_secret and not token:
        return
    raise HTTPException(status_code=403, detail="Invalid or expired source pack download token")


@router.get("/help")
def knowledge_help(_: None = Depends(require_knowledge_secret)) -> dict[str, Any]:
    return {"help": knowledge_help_text()}


@router.post("/commands")
def create_knowledge_command(
    payload: KnowledgeCommandIn,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_knowledge_secret),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    command = parse_knowledge_command(payload.text)
    if command.help_requested:
        return {"ok": True, "help": knowledge_help_text()}
    if command.action == "status":
        job = job_by_key(db, command.status_key) or latest_job_for_context(db, payload.chat_id, payload.requester)
        return {"ok": True, "job": serialize_or_404(job)}

    job = orchestrator.create_job(
        db,
        command,
        request_text=payload.text,
        source=payload.source,
        requester=payload.requester,
        chat_id=payload.chat_id,
        source_event_id=payload.source_event_id,
    )
    if payload.run_async and job.status == "queued":
        background_tasks.add_task(orchestrator.run_job, job.id)
    elif job.status == "queued":
        orchestrator.run_job(job.id)
        db.refresh(job)
    return {"ok": True, "job": orchestrator.serialize_job(job)}


@router.get("/jobs")
def list_knowledge_jobs(
    limit: int = 20,
    _: None = Depends(require_knowledge_secret),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    jobs = db.scalars(select(KnowledgeJob).order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id.desc()).limit(limit)).all()
    return {"jobs": [orchestrator.serialize_job(job) for job in jobs]}


@router.get("/jobs/{job_key}")
def read_knowledge_job(
    job_key: str,
    _: None = Depends(require_knowledge_secret),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return serialize_or_404(job_by_key(db, job_key))


@router.get("/jobs/{job_key}/source-pack/{kind}")
def download_source_pack(
    job_key: str,
    kind: str,
    request: Request,
    x_knowledge_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> FileResponse:
    job = job_by_key(db, job_key)
    if job is None:
        raise HTTPException(status_code=404, detail="Knowledge job not found")
    authorize_download(request, job, kind, x_knowledge_secret)
    path = source_pack_path_or_404(job, kind)
    media_type = "text/markdown; charset=utf-8" if path.suffix.lower() == ".md" else "text/plain; charset=utf-8"
    return FileResponse(path, media_type=media_type, filename=path.name)


@feishu_router.post("/events")
async def receive_feishu_knowledge_event(
    request: Request,
    background_tasks: BackgroundTasks,
    x_knowledge_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payload = await request.json()
    if "encrypt" in payload:
        raise HTTPException(status_code=400, detail="Encrypted Feishu callbacks are not enabled for this endpoint")
    if payload.get("type") == "url_verification" or payload.get("challenge"):
        verify_feishu_or_shared_secret(payload, request, x_knowledge_secret)
        return {"challenge": payload.get("challenge")}

    verify_feishu_or_shared_secret(payload, request, x_knowledge_secret)
    event = payload.get("event") or {}
    message = event.get("message") or {}
    text = extract_feishu_text(message)
    if not text:
        return {"ok": True, "ignored": "empty_or_non_text_message"}

    chat_id = message.get("chat_id")
    event_id = (payload.get("header") or {}).get("event_id") or message.get("message_id")
    sender = (event.get("sender") or {}).get("sender_id") or {}
    requester = sender.get("open_id") or sender.get("user_id") or sender.get("union_id")
    command = parse_knowledge_command(text)

    if command.help_requested:
        background_tasks.add_task(orchestrator.feishu.send_text, knowledge_help_text(), chat_id)
        return {"ok": True, "action": "help"}

    if command.action == "status":
        job = job_by_key(db, command.status_key) or latest_job_for_context(db, chat_id, requester)
        if job is None:
            background_tasks.add_task(orchestrator.feishu.send_text, "还没有找到知识库任务。", chat_id)
            return {"ok": True, "action": "status", "found": False}
        summary = "\n".join(
            [
                f"任务：{job.job_key}",
                f"状态：{job.status}",
                f"主题：{job.title or job.topic or '-'}",
                f"Notion：{job.notion_url or '-'}",
                f"Drive：{job.drive_url or '-'}",
                f"研究工作台：{job.notebooklm_url or settings.ima_workspace_url or '-'}",
                f"Markdown 下载：{orchestrator.source_pack_download_urls(job).get('md') or '-'}",
                f"Text 下载：{orchestrator.source_pack_download_urls(job).get('txt') or '-'}",
                f"错误：{job.error_text or '-'}",
            ]
        )
        background_tasks.add_task(orchestrator.feishu.send_text, summary, chat_id)
        return {"ok": True, "action": "status", "job_key": job.job_key}

    job = orchestrator.create_job(
        db,
        command,
        request_text=text,
        source="feishu",
        requester=requester,
        chat_id=chat_id,
        source_event_id=event_id,
    )
    if job.status == "queued":
        background_tasks.add_task(
            orchestrator.feishu.send_text,
            f"已收到知识库任务：{job.job_key}\n主题：{job.topic or job.title}",
            chat_id,
        )
        background_tasks.add_task(orchestrator.run_job, job.id)
    return {"ok": True, "job_key": job.job_key, "status": job.status}
