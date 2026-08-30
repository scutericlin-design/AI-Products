from __future__ import annotations

import json
import base64
import hashlib
import hmac
import os
import re
import shlex
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, settings
from app.database import SessionLocal
from app.models import KnowledgeJob


@dataclass
class ParsedKnowledgeCommand:
    action: str
    topic: str | None
    title: str | None = None
    content: str | None = None
    status_key: str | None = None
    help_requested: bool = False


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def slugify(value: str, fallback: str = "knowledge-topic") -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:80] or fallback


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _download_secret() -> str:
    return settings.knowledge_command_secret or settings.app_secret


def sign_source_pack_token(job_key: str, kind: str, ttl_seconds: int | None = None) -> str:
    expires_at = int(datetime.utcnow().timestamp()) + int(ttl_seconds or settings.knowledge_download_token_ttl_seconds)
    payload = _base64url_encode(
        json.dumps(
            {"exp": expires_at, "j": job_key, "k": kind},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = hmac.new(_download_secret().encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    return f"{payload}.{_base64url_encode(signature)}"


def verify_source_pack_token(token: str, job_key: str, kind: str) -> bool:
    try:
        payload, signature = token.split(".", 1)
        expected = hmac.new(_download_secret().encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
        actual = _base64url_decode(signature)
        if not hmac.compare_digest(expected, actual):
            return False
        data = json.loads(_base64url_decode(payload).decode("utf-8"))
        if data.get("j") != job_key or data.get("k") != kind:
            return False
        return int(data.get("exp") or 0) >= int(datetime.utcnow().timestamp())
    except Exception:
        return False


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.S)
    if fenced:
        cleaned = fenced.group(1)
    if cleaned.startswith("{"):
        return json.loads(cleaned)
    for index in [match.start() for match in re.finditer(r"\{", cleaned)]:
        candidate = cleaned[index:].strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError("No JSON object found in LLM response.")


def parse_knowledge_command(text: str) -> ParsedKnowledgeCommand:
    original = text.strip()
    cleaned = re.sub(r"<at[^>]*>.*?</at>", "", original).strip()
    lowered = cleaned.lower()

    if lowered in {"kb", "/kb", "kb help", "/kb help", "帮助", "知识库帮助"}:
        return ParsedKnowledgeCommand(action="help", topic=None, help_requested=True)

    status_match = re.match(r"^(?:/)?kb\s+status(?:\s+(.+))?$", cleaned, flags=re.I)
    if status_match:
        return ParsedKnowledgeCommand(action="status", topic=None, status_key=(status_match.group(1) or "").strip())

    sync_match = re.match(r"^(?:/)?kb\s+sync\s+(.+)$", cleaned, flags=re.I)
    if sync_match:
        title = sync_match.group(1).strip()
        return ParsedKnowledgeCommand(action="sync", topic=title, title=title)

    add_match = re.match(r"^(?:/)?kb\s+(?:add|append|追加|补充)\s+(.+?)(?:\r?\n+([\s\S]+))?$", cleaned, flags=re.I)
    if add_match:
        title = add_match.group(1).strip()
        content = (add_match.group(2) or "").strip()
        if not content:
            inline_match = re.match(r"^(.+?)(?:\s+::\s+|\s+\|\|\s+)([\s\S]+)$", title)
            if inline_match:
                title = inline_match.group(1).strip()
                content = inline_match.group(2).strip()
        content = re.sub(r"^\s*(?:-{3,}|内容[:：])\s*", "", content).strip()
        return ParsedKnowledgeCommand(action="add", topic=title, title=title, content=content)

    research_match = re.match(r"^(?:/)?kb\s+(?:research|研究)\s+(.+)$", cleaned, flags=re.I)
    if research_match:
        topic = research_match.group(1).strip()
        return ParsedKnowledgeCommand(action="research", topic=topic)

    cn_research_match = re.match(r"^(?:研究主题|专题研究|知识库研究|研究)[:：\s]+(.+)$", cleaned, flags=re.I)
    if cn_research_match:
        topic = cn_research_match.group(1).strip()
        return ParsedKnowledgeCommand(action="research", topic=topic)

    if lowered.startswith("sync "):
        title = cleaned[5:].strip()
        return ParsedKnowledgeCommand(action="sync", topic=title, title=title)

    return ParsedKnowledgeCommand(action="research", topic=cleaned)


def knowledge_help_text() -> str:
    workspace_label = settings.knowledge_study_workspace_label or settings.knowledge_study_workspace
    return "\n".join(
        [
            "Hermes 知识库命令",
            "",
            f"kb research <主题>  创建 Notion 专题、生成 Source Pack，并同步 {workspace_label} 研究工作台",
            f"kb sync <Notion Research Pack 标题>  重新导出同名专题的 {workspace_label} Source Pack",
            f"kb add <Notion Research Pack 标题> 换行 <新内容>  追加内容到专题，并重新导出 {workspace_label} Source Pack",
            "kb status <任务号>  查看任务状态",
            "",
            "例子：",
            "kb research 如何提高中学生英语水平，并在高考中取得优异成绩",
            "kb add 2026-06 中学生英语提升与高考优胜策略\n这是一条新资料或新观察……",
        ]
    )


class FeishuMessenger:
    def __init__(self) -> None:
        self._tenant_access_token: str | None = None
        self._token_expires_at: datetime | None = None

    def send_text(self, text: str, chat_id: str | None = None) -> dict[str, Any]:
        if not settings.feishu_reply_enabled:
            return {"status": "disabled"}
        if chat_id and settings.feishu_bot_app_id and settings.feishu_bot_app_secret:
            return self._send_bot_text(chat_id, text)
        webhook = settings.knowledge_feishu_webhook_url or settings.trading_feishu_webhook_url
        if webhook:
            return self._send_webhook_text(webhook, text)
        return {"status": "skipped_no_feishu_channel"}

    def _send_webhook_text(self, webhook: str, text: str) -> dict[str, Any]:
        payload = {"msg_type": "text", "content": {"text": text}}
        try:
            response = requests.post(webhook, json=payload, timeout=12)
            return {
                "status": "sent" if response.ok else "failed",
                "response_code": response.status_code,
                "response_text": response.text[:1000],
            }
        except Exception as exc:
            return {"status": "failed", "error": str(exc)[:1000]}

    def _tenant_token(self) -> str:
        if self._tenant_access_token and self._token_expires_at and self._token_expires_at > datetime.utcnow():
            return self._tenant_access_token
        payload = {
            "app_id": settings.feishu_bot_app_id,
            "app_secret": settings.feishu_bot_app_secret,
        }
        response = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json=payload,
            timeout=12,
        )
        response.raise_for_status()
        data = response.json()
        token = data.get("tenant_access_token")
        if not token:
            raise RuntimeError(f"Feishu tenant token missing: {data}")
        expires_in = int(data.get("expire") or 3600)
        self._tenant_access_token = token
        self._token_expires_at = datetime.utcnow() + timedelta(seconds=max(expires_in - 120, 60))
        return token

    def _send_bot_text(self, chat_id: str, text: str) -> dict[str, Any]:
        token = self._tenant_token()
        payload = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        response = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=12,
        )
        return {
            "status": "sent" if response.ok else "failed",
            "response_code": response.status_code,
            "response_text": response.text[:1000],
        }


class ResearchPackBuilder:
    def build(self, topic: str) -> dict[str, Any]:
        llm_pack = self._build_with_llm(topic)
        if llm_pack:
            return self._normalize_pack(llm_pack, topic)
        return self._fallback_pack(topic)

    def _build_with_llm(self, topic: str) -> dict[str, Any] | None:
        api_key = settings.llm_api_key
        base_url = settings.llm_base_url
        model = settings.llm_model
        if (settings.llm_provider or "").lower() == "siliconflow":
            api_key = api_key or settings.siliconflow_api_key
            base_url = base_url or settings.siliconflow_base_url
            model = settings.siliconflow_model or model
        if not api_key or not base_url:
            return None

        prompt = self._prompt(topic)
        try:
            response = requests.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是严谨的中文知识库研究员，只输出合法 JSON，不要输出 Markdown。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                },
                timeout=90,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return extract_json_object(content)
        except Exception:
            return None

    def _prompt(self, topic: str) -> str:
        today = date.today().isoformat()
        return f"""
请为个人知识库生成一个 Notion Research Pack JSON。主题：{topic}

要求：
1. 当前日期：{today}。
2. 输出必须是一个 JSON object，不要 Markdown。
3. 字段必须兼容以下 schema：
{{
  "title": "YYYY-MM 中文专题标题",
  "question": "...",
  "status": "Done",
  "topic": ["..."],
  "priority": "High|Medium|Low",
  "onyx_agent": "Personal Knowledge Recall",
  "source_count": 0,
  "key_takeaways": "...",
  "decision": "...",
  "next_action": "...",
  "ai_index": true,
  "created": "{today}",
  "review_date": "YYYY-MM-DD",
  "body": [{{"type": "heading_2|heading_3|paragraph|bulleted_list_item", "text": "..."}}],
  "sources": [],
  "evergreen_notes": [],
  "decisions": [],
  "ai_outputs": []
}}
4. body 至少包含 Executive Summary、Evidence Map、Action Plan、Study Workspace Prompts、Risks。
5. 不要编造具体 URL；没有确定来源时 sources 留空，在 body 中标记“待补充来源”。
""".strip()

    def _normalize_pack(self, pack: dict[str, Any], topic: str) -> dict[str, Any]:
        today = date.today()
        pack.setdefault("title", f"{today:%Y-%m} {topic[:48]}")
        pack.setdefault("question", topic)
        pack.setdefault("status", "Done")
        pack.setdefault("topic", ["Knowledge"])
        pack.setdefault("priority", "Medium")
        pack.setdefault("onyx_agent", "Personal Knowledge Recall")
        pack.setdefault("source_count", len(pack.get("sources", [])))
        pack.setdefault("key_takeaways", "")
        pack.setdefault("decision", "")
        pack.setdefault("next_action", "把 Source Pack 导入专题研究工作台后做问答，并把结论回写 Notion。")
        pack.setdefault("ai_index", True)
        pack.setdefault("created", today.isoformat())
        pack.setdefault("review_date", (today + timedelta(days=14)).isoformat())
        pack.setdefault("body", [])
        for key in ("sources", "evergreen_notes", "decisions", "ai_outputs"):
            pack.setdefault(key, [])
        if not pack["body"]:
            pack["body"] = self._fallback_body(topic)
        return pack

    def _fallback_pack(self, topic: str) -> dict[str, Any]:
        today = date.today()
        return {
            "title": f"{today:%Y-%m} {topic[:48]}",
            "question": topic,
            "status": "Done",
            "topic": ["Knowledge", "Research"],
            "priority": "Medium",
            "onyx_agent": "Personal Knowledge Recall",
            "source_count": 0,
            "key_takeaways": "已创建专题研究骨架；需要后续补充外部来源、证据表和专题研究工作台输出。",
            "decision": "先把主题纳入 Notion 主库，并生成专题 Source Pack，后续通过飞书持续追加资料和结论。",
            "next_action": "把 Source Pack 导入 ima 等专题研究工作台，然后用内置 prompts 做专题研究。",
            "ai_index": True,
            "created": today.isoformat(),
            "review_date": (today + timedelta(days=14)).isoformat(),
            "body": self._fallback_body(topic),
            "sources": [],
            "evergreen_notes": [
                {
                    "name": f"{topic[:40]} - 研究入口",
                    "type": "Insight",
                    "topic": ["Knowledge"],
                    "status": "Draft",
                    "confidence": "Medium",
                    "summary": "该专题由 Hermes 飞书命令自动创建，后续结论以 Notion 为主库沉淀。",
                    "ai_index": True,
                }
            ],
            "decisions": [],
            "ai_outputs": [],
        }

    def _fallback_body(self, topic: str) -> list[dict[str, str]]:
        return [
            {"type": "heading_2", "text": "Executive Summary"},
            {
                "type": "paragraph",
                "text": f"本专题围绕“{topic}”建立研究包。Hermes 已完成 Notion 入库和专题 Source Pack 准备，后续应补充权威来源、证据表和可执行方案。",
            },
            {"type": "heading_2", "text": "Research Questions"},
            {"type": "bulleted_list_item", "text": "这个主题真正要解决的决策问题是什么？"},
            {"type": "bulleted_list_item", "text": "有哪些事实、经验和反例需要被专题研究工作台对照消化？"},
            {"type": "bulleted_list_item", "text": "哪些结论值得沉淀成 Evergreen Notes？"},
            {"type": "heading_2", "text": "Action Plan"},
            {"type": "bulleted_list_item", "text": "补充 5-10 个高质量来源。"},
            {"type": "bulleted_list_item", "text": "用 ima 或同类专题研究工作台生成 Source Map、Evidence Table 和 Decision Memo。"},
            {"type": "bulleted_list_item", "text": "把最终结论回写到 Notion Research Pack。"},
            {"type": "heading_2", "text": "Study Workspace Prompts"},
            {"type": "bulleted_list_item", "text": "请基于全部来源生成 Source Map，列出每个来源的核心观点、适合回答的问题和可信度。"},
            {"type": "bulleted_list_item", "text": "请生成 Evidence Table，明确区分事实、推论、建议和不确定性。"},
            {"type": "bulleted_list_item", "text": "请写一份 Decision Memo，并给出可执行的下一步。"},
            {"type": "heading_2", "text": "Risks"},
            {"type": "bulleted_list_item", "text": "当前自动生成内容可能缺少外部证据，需要后续补充来源验证。"},
        ]


class NotionKnowledgeBridge:
    def __init__(self) -> None:
        self.stack_dir = settings.knowledge_stack_dir

    def create_research_pack(self, input_json: Path) -> dict[str, Any]:
        script = self.stack_dir / "scripts" / "create_research_pack_from_json.py"
        return self._run_script(script, ["--input-json", str(input_json)])

    def append_to_research_pack(
        self,
        title: str,
        content: str,
        source: str | None = None,
        requester: str | None = None,
    ) -> dict[str, Any]:
        if not content.strip():
            raise RuntimeError("Add content is empty.")
        page = self._find_research_pack(title)
        captured_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        source_label = source or "Hermes"
        meta = f"Captured via {source_label} at {captured_at}"
        if requester:
            meta += f" · requester={requester}"
        blocks = [
            {"object": "block", "type": "divider", "divider": {}},
            self._text_block("heading_2", f"Inbox Capture - {captured_at}"),
            self._text_block("paragraph", meta),
            *self._content_blocks(content),
        ]
        added_blocks = 0
        for index in range(0, len(blocks), 90):
            chunk = blocks[index : index + 90]
            self._notion_request("PATCH", f"/blocks/{page['id']}/children", {"children": chunk})
            added_blocks += len(chunk)
        return {
            "status": "appended",
            "id": page["id"],
            "url": page.get("url"),
            "title": title,
            "added_blocks": added_blocks,
            "captured_at": captured_at,
        }

    def export_study_source_pack(self, title: str | None = None, page_id: str | None = None) -> dict[str, Any]:
        script = self.stack_dir / "scripts" / "export_notion_to_notebooklm_pack.py"
        args = [
            "--output-dir",
            str(settings.knowledge_source_packs_dir),
            "--update-notion",
            "--target-label",
            settings.knowledge_study_workspace_label or settings.knowledge_study_workspace,
        ]
        if page_id:
            args.extend(["--research-pack-id", page_id])
        elif title:
            args.extend(["--research-pack-title", title])
        else:
            raise RuntimeError("Need research pack title or page_id to export study source pack.")
        return self._run_script(script, args)

    def _notion_headers(self) -> dict[str, str]:
        if not settings.notion_api_key:
            raise RuntimeError("NOTION_API_KEY is not configured.")
        return {
            "Authorization": f"Bearer {settings.notion_api_key}",
            "Notion-Version": settings.notion_version,
            "Content-Type": "application/json",
        }

    def _notion_request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.request(
            method,
            f"https://api.notion.com/v1{path}",
            headers=self._notion_headers(),
            json=payload,
            timeout=45,
        )
        if not response.ok:
            raise RuntimeError(f"Notion API error {response.status_code} for {method} {path}: {response.text[:2000]}")
        return response.json()

    def _load_setup(self) -> dict[str, Any]:
        if not settings.notion_setup_json.exists():
            raise RuntimeError(f"Notion setup json not found: {settings.notion_setup_json}")
        return json.loads(settings.notion_setup_json.read_text(encoding="utf-8"))

    def _database_id(self, setup: dict[str, Any], name: str) -> str:
        try:
            return setup["databases"][name]["id"]
        except KeyError as exc:
            raise RuntimeError(f"Database not found in setup json: {name}") from exc

    def _find_research_pack(self, title: str) -> dict[str, Any]:
        setup = self._load_setup()
        db_id = self._database_id(setup, "Research Packs")
        payload = {
            "filter": {"property": "Name", "title": {"equals": title}},
            "page_size": 1,
        }
        response = self._notion_request("POST", f"/databases/{db_id}/query", payload)
        rows = response.get("results") or []
        if not rows:
            raise RuntimeError(f"No Research Pack found with title: {title}")
        return rows[0]

    def _rich_text(self, content: str) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        remaining = content
        while remaining:
            part = remaining[:1900]
            remaining = remaining[1900:]
            chunks.append({"type": "text", "text": {"content": part}})
        return chunks

    def _text_block(self, block_type: str, text: str) -> dict[str, Any]:
        return {
            "object": "block",
            "type": block_type,
            block_type: {"rich_text": self._rich_text(text)},
        }

    def _content_blocks(self, content: str) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        paragraph_lines: list[str] = []

        def flush_paragraph() -> None:
            if not paragraph_lines:
                return
            text = "\n".join(paragraph_lines).strip()
            paragraph_lines.clear()
            if text:
                blocks.append(self._text_block("paragraph", text))

        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                flush_paragraph()
                continue
            if re.match(r"https?://\S+$", line):
                flush_paragraph()
                blocks.append({"object": "block", "type": "bookmark", "bookmark": {"url": line}})
                continue
            heading_match = re.match(r"^#{1,3}\s+(.+)$", line)
            if heading_match:
                flush_paragraph()
                blocks.append(self._text_block("heading_3", heading_match.group(1).strip()))
                continue
            bullet_match = re.match(r"^(?:[-*]|[0-9]+[.)])\s+(.+)$", line)
            if bullet_match:
                flush_paragraph()
                blocks.append(self._text_block("bulleted_list_item", bullet_match.group(1).strip()))
                continue
            paragraph_lines.append(line)

        flush_paragraph()
        if not blocks:
            blocks.append(self._text_block("paragraph", content.strip()))
        return blocks

    def _run_script(self, script: Path, args: list[str]) -> dict[str, Any]:
        if not settings.notion_api_key:
            raise RuntimeError("NOTION_API_KEY is not configured.")
        if not settings.notion_setup_json.exists():
            raise RuntimeError(f"Notion setup json not found: {settings.notion_setup_json}")
        if not script.exists():
            raise RuntimeError(f"Knowledge script not found: {script}")
        env = {
            **dict(os.environ),
            "NOTION_API_KEY": settings.notion_api_key,
            "NOTION_VERSION": settings.notion_version,
        }
        command = [sys.executable, str(script), "--setup-json", str(settings.notion_setup_json), *args]
        result = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            raise RuntimeError(f"{script.name} failed: {(stderr or stdout)[:2000]}")
        return self._parse_stdout_json(result.stdout)

    def _parse_stdout_json(self, stdout: str) -> dict[str, Any]:
        cleaned = stdout.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        for index in [match.start() for match in re.finditer(r"\{", cleaned)]:
            try:
                return json.loads(cleaned[index:])
            except json.JSONDecodeError:
                continue
        raise RuntimeError(f"Could not parse script JSON output: {cleaned[:1000]}")


class DriveSourcePackSync:
    SCOPES = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/documents",
    ]

    def sync(self, manifest: dict[str, Any]) -> dict[str, Any]:
        if not settings.google_drive_enabled:
            return {"status": "skipped_disabled"}
        if not settings.google_drive_folder_id:
            return {"status": "skipped_no_folder_id"}
        try:
            return self._sync_with_google_api(manifest)
        except ImportError as exc:
            return {"status": "failed_missing_google_libs", "error": str(exc)}
        except Exception as exc:
            return {"status": "failed", "error": str(exc)[:2000]}

    def _sync_with_google_api(self, manifest: dict[str, Any]) -> dict[str, Any]:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaInMemoryUpload

        credentials, auth_mode = self._credentials()
        drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
        docs = build("docs", "v1", credentials=credentials, cache_discovery=False)

        source_path_value = manifest.get("text_path") or manifest.get("markdown_path")
        if not source_path_value:
            raise RuntimeError("Source pack manifest does not include text_path or markdown_path.")
        source_path = Path(source_path_value)
        if not source_path.exists():
            raise RuntimeError(f"Source pack file not found: {source_path}")
        title = manifest.get("title") or source_path.stem
        doc_name = f"{settings.knowledge_study_workspace_label or settings.knowledge_study_workspace} Source Pack - {title}"
        text = source_path.read_text(encoding="utf-8")

        existing = self._find_drive_doc(drive, doc_name)
        if existing:
            file_id = existing["id"]
            self._replace_doc_text(docs, file_id, text)
            file = drive.files().get(
                fileId=file_id,
                fields="id,name,webViewLink",
                supportsAllDrives=True,
            ).execute()
            status = "updated"
        else:
            media = MediaInMemoryUpload(text.encode("utf-8"), mimetype="text/plain", resumable=False)
            metadata: dict[str, Any] = {
                "name": doc_name,
                "mimeType": "application/vnd.google-apps.document",
                "parents": [settings.google_drive_folder_id],
            }
            file = (
                drive.files()
                .create(
                    body=metadata,
                    media_body=media,
                    fields="id,name,webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
            file_id = file["id"]
            status = "created"

        share_warning = None
        if settings.google_drive_share_with_email:
            try:
                drive.permissions().create(
                    fileId=file_id,
                    body={"type": "user", "role": "writer", "emailAddress": settings.google_drive_share_with_email},
                    sendNotificationEmail=False,
                    supportsAllDrives=True,
                ).execute()
            except Exception as exc:
                share_warning = str(exc)[:1000]

        result = {
            "status": status,
            "file_id": file_id,
            "name": file.get("name") or doc_name,
            "url": file.get("webViewLink"),
            "source_type": "Google Drive Doc",
            "auth_mode": auth_mode,
        }
        if share_warning:
            result["share_warning"] = share_warning
        return result

    def _credentials(self) -> tuple[Any, str]:
        oauth_info = self._oauth_token_info()
        if oauth_info:
            return self._oauth_credentials(oauth_info), "oauth_user"

        from google.oauth2 import service_account

        credentials_info = self._service_account_info()
        return (
            service_account.Credentials.from_service_account_info(
                credentials_info,
                scopes=self.SCOPES,
            ),
            "service_account",
        )

    def _oauth_token_info(self) -> dict[str, Any] | None:
        if settings.google_oauth_token_json:
            return json.loads(settings.google_oauth_token_json)
        if settings.google_oauth_token_file and settings.google_oauth_token_file.exists():
            return json.loads(settings.google_oauth_token_file.read_text(encoding="utf-8"))
        return None

    def _oauth_credentials(self, token_info: dict[str, Any]) -> Any:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        credentials = Credentials.from_authorized_user_info(token_info, scopes=self.SCOPES)
        if not credentials.valid:
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                self._persist_oauth_credentials(credentials)
            else:
                raise RuntimeError("Google OAuth token is invalid and cannot be refreshed.")
        return credentials

    def _persist_oauth_credentials(self, credentials: Any) -> None:
        if settings.google_oauth_token_file:
            settings.google_oauth_token_file.parent.mkdir(parents=True, exist_ok=True)
            settings.google_oauth_token_file.write_text(credentials.to_json(), encoding="utf-8")

    def _service_account_info(self) -> dict[str, Any]:
        if settings.google_service_account_json:
            return json.loads(settings.google_service_account_json)
        if settings.google_service_account_file and settings.google_service_account_file.exists():
            return json.loads(settings.google_service_account_file.read_text(encoding="utf-8"))
        raise RuntimeError("Google service account is not configured.")

    def _find_drive_doc(self, drive: Any, name: str) -> dict[str, Any] | None:
        escaped_name = name.replace("\\", "\\\\").replace("'", "\\'")
        query = (
            f"name = '{escaped_name}' and "
            f"'{settings.google_drive_folder_id}' in parents and "
            "mimeType = 'application/vnd.google-apps.document' and trashed = false"
        )
        result = drive.files().list(
            q=query,
            spaces="drive",
            fields="files(id,name,webViewLink)",
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = result.get("files", [])
        return files[0] if files else None

    def _replace_doc_text(self, docs: Any, document_id: str, text: str) -> None:
        document = docs.documents().get(documentId=document_id).execute()
        content = document.get("body", {}).get("content", [])
        end_index = content[-1].get("endIndex", 1) if content else 1
        requests_payload: list[dict[str, Any]] = []
        if end_index > 2:
            requests_payload.append({"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end_index - 1}}})
        requests_payload.append({"insertText": {"location": {"index": 1}, "text": text}})
        docs.documents().batchUpdate(documentId=document_id, body={"requests": requests_payload}).execute()


class OnyxBridge:
    def notify(self, job: KnowledgeJob, manifest: dict[str, Any] | None) -> dict[str, Any]:
        if settings.onyx_sync_webhook_url:
            headers = {"Content-Type": "application/json"}
            if settings.onyx_api_key:
                headers["Authorization"] = f"Bearer {settings.onyx_api_key}"
            payload = {
                "job_key": job.job_key,
                "title": job.title,
                "notion_url": job.notion_url,
                "manifest": manifest,
            }
            try:
                response = requests.post(settings.onyx_sync_webhook_url, headers=headers, json=payload, timeout=20)
                return {
                    "status": "notified" if response.ok else "failed",
                    "response_code": response.status_code,
                    "response_text": response.text[:1000],
                }
            except Exception as exc:
                return {"status": "failed", "error": str(exc)[:1000]}
        if settings.onyx_workspace_url:
            return {
                "status": "notion_connector_auto_index",
                "workspace_url": settings.onyx_workspace_url,
            }
        return {"status": "skipped_no_onyx_config"}


class StudyWorkspaceBridge:
    def build_result(self, manifest: dict[str, Any] | None, drive_result: dict[str, Any] | None) -> dict[str, Any]:
        workspace = (settings.knowledge_study_workspace or "ima").strip().lower()
        if workspace == "notebooklm":
            return self._build_notebooklm_result(manifest, drive_result)
        return self._build_ima_result(manifest)

    def _build_ima_result(self, manifest: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "status": "source_pack_ready",
            "target": "ima",
            "workspace_url": settings.ima_workspace_url,
            "space_url": settings.ima_default_space_url,
            "automation_mode": "source_pack_and_llm_outputs",
            "api_status": "no_public_server_api_configured",
            "recommended_source_type": "file upload / copied text",
            "source_url": None,
            "manifest": manifest,
            "prompts": [
                "请先生成 Source Map，列出每个来源的核心观点、适合回答的问题、可信度和冲突点。",
                "请生成 Evidence Table，列出 Claim / Evidence / Source / Confidence / Caveat。",
                "请写一份 Decision Memo，并提炼 3-7 条适合回写 Notion 的 Evergreen Notes。",
            ],
            "note": "ima 官方页面未提供已配置的服务端 API；Hermes 会稳定生成 ima Source Pack、Notion 记录和 LLM 研究输出。",
        }

    def _build_notebooklm_result(self, manifest: dict[str, Any] | None, drive_result: dict[str, Any] | None) -> dict[str, Any]:
        source_url = (drive_result or {}).get("url")
        return {
            "status": "source_pack_ready",
            "target": "notebooklm",
            "workspace_url": settings.notebooklm_workspace_url,
            "notebook_url": settings.notebooklm_default_notebook_url,
            "recommended_source_type": "Google Drive Doc" if source_url else "Copied text / file upload",
            "source_url": source_url,
            "manifest": manifest,
            "prompts": [
                "请先生成 Source Map，列出每个来源的核心观点、适合回答的问题、可信度和冲突点。",
                "请生成 Evidence Table，列出 Claim / Evidence / Source / Confidence / Caveat。",
                "请写一份 Decision Memo，并提炼 3-7 条适合回写 Notion 的 Evergreen Notes。",
            ],
        }


class GitHubKnowledgeArchive:
    def archive(
        self,
        job: KnowledgeJob,
        manifest: dict[str, Any] | None,
        drive_result: dict[str, Any] | None,
        study_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not settings.knowledge_github_archive_enabled:
            return {"status": "skipped_disabled"}
        if not settings.knowledge_github_archive_repo_url:
            return {"status": "skipped_no_repo_url"}
        if not manifest:
            return {"status": "skipped_no_manifest"}

        try:
            repo_dir = settings.knowledge_github_archive_dir
            repo_dir.mkdir(parents=True, exist_ok=True)
            self._ensure_repo(repo_dir)
            written = self._write_archive_files(repo_dir, job, manifest, drive_result, study_result)
            if not written:
                return {"status": "skipped_no_archive_files"}
            status = self._git(repo_dir, ["status", "--porcelain"])
            if not status.strip():
                return {
                    "status": "unchanged",
                    "repo_dir": str(repo_dir),
                    "branch": settings.knowledge_github_archive_branch,
                    "files": written,
                }

            self._git(repo_dir, ["add", "--", *written])
            commit_message = f"Archive knowledge pack: {job.title or job.topic or job.job_key} ({job.job_key})"
            self._git(
                repo_dir,
                [
                    "-c",
                    f"user.name={settings.knowledge_github_archive_author_name}",
                    "-c",
                    f"user.email={settings.knowledge_github_archive_author_email}",
                    "commit",
                    "-m",
                    commit_message,
                ],
            )
            commit_sha = self._git(repo_dir, ["rev-parse", "HEAD"]).strip()
            push_result: dict[str, Any] = {"status": "skipped_disabled"}
            if settings.knowledge_github_archive_push:
                self._git(
                    repo_dir,
                    [
                        "push",
                        "-u",
                        "origin",
                        f"HEAD:{settings.knowledge_github_archive_branch}",
                    ],
                )
                push_result = {"status": "pushed"}
            return {
                "status": "archived",
                "repo_url": settings.knowledge_github_archive_repo_url,
                "repo_dir": str(repo_dir),
                "branch": settings.knowledge_github_archive_branch,
                "commit": commit_sha,
                "push": push_result,
                "files": written,
            }
        except Exception as exc:
            return {"status": "failed", "error": str(exc)[:2000]}

    def _ensure_repo(self, repo_dir: Path) -> None:
        git_dir = repo_dir / ".git"
        if not git_dir.exists() and any(repo_dir.iterdir()):
            raise RuntimeError(f"Archive directory is not empty and is not a git repo: {repo_dir}")
        if not git_dir.exists():
            self._git(repo_dir, ["init"])
            self._git(repo_dir, ["remote", "add", "origin", settings.knowledge_github_archive_repo_url or ""])
        else:
            remotes = self._git(repo_dir, ["remote"]).splitlines()
            if "origin" not in remotes:
                self._git(repo_dir, ["remote", "add", "origin", settings.knowledge_github_archive_repo_url or ""])
            else:
                current = self._git(repo_dir, ["remote", "get-url", "origin"]).strip()
                if current != settings.knowledge_github_archive_repo_url:
                    self._git(repo_dir, ["remote", "set-url", "origin", settings.knowledge_github_archive_repo_url or ""])

        branch = settings.knowledge_github_archive_branch or "main"
        fetch = self._git(repo_dir, ["fetch", "origin", branch], check=False)
        if "fatal:" not in fetch.lower():
            self._git(repo_dir, ["checkout", "-B", branch, f"origin/{branch}"], check=False)
        current_branch = self._git(repo_dir, ["branch", "--show-current"], check=False).strip()
        if current_branch != branch:
            self._git(repo_dir, ["checkout", "-B", branch])

    def _write_archive_files(
        self,
        repo_dir: Path,
        job: KnowledgeJob,
        manifest: dict[str, Any],
        drive_result: dict[str, Any] | None,
        study_result: dict[str, Any] | None,
    ) -> list[str]:
        title = manifest.get("title") or job.title or job.topic or job.job_key
        slug = slugify(title)
        prefix = settings.knowledge_github_archive_path_prefix.strip().strip("/")
        base_prefix = f"{prefix}/" if prefix else ""
        workspace = (settings.knowledge_study_workspace_label or settings.knowledge_study_workspace or "study").lower()
        job_key = job.job_key
        created_at = (job.created_at or datetime.utcnow()).isoformat()
        archived_at = datetime.utcnow().isoformat()

        markdown_path_value = manifest.get("markdown_path")
        if not markdown_path_value:
            return []
        markdown_path = Path(markdown_path_value)
        if not markdown_path.exists() or not markdown_path.is_file():
            return []
        markdown = markdown_path.read_text(encoding="utf-8")

        version_dir = f"{base_prefix}source-packs/{workspace}/{slug}"
        version_md = f"{version_dir}/{job_key}.md"
        latest_md = f"{version_dir}/latest.md"
        research_latest = f"{base_prefix}research-packs/{slug}.md"
        manifest_path = f"{base_prefix}manifests/{workspace}/{slug}/{job_key}.json"
        index_path = f"{base_prefix}index.json"
        readme_path = f"{base_prefix}README.md" if base_prefix else "README.md"

        metadata = {
            "job_key": job_key,
            "action": job.action,
            "title": title,
            "topic": job.topic,
            "notion_url": job.notion_url or manifest.get("research_pack_url"),
            "drive_url": (drive_result or {}).get("url") or job.drive_url,
            "study_workspace": {
                "target": (study_result or {}).get("target"),
                "workspace_url": (study_result or {}).get("workspace_url"),
                "notebook_url": (study_result or {}).get("notebook_url"),
                "source_url": (study_result or {}).get("source_url"),
            },
            "source_pack": {
                "title": manifest.get("title"),
                "research_pack_id": manifest.get("research_pack_id"),
                "export_hash": manifest.get("export_hash"),
                "generated_at": manifest.get("generated_at"),
                "target_label": manifest.get("target_label"),
                "recommended_source_type": manifest.get("recommended_source_type"),
            },
            "created_at": created_at,
            "archived_at": archived_at,
        }

        self._write_text(repo_dir, version_md, markdown)
        self._write_text(repo_dir, latest_md, markdown)
        self._write_text(repo_dir, research_latest, markdown)
        self._write_text(repo_dir, manifest_path, json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        if settings.knowledge_github_archive_include_text:
            text_path = Path(manifest.get("text_path") or "")
            if text_path.exists():
                self._write_text(repo_dir, f"{version_dir}/{job_key}.txt", text_path.read_text(encoding="utf-8"))

        index = self._read_json(repo_dir / index_path, {"items": []})
        items = [item for item in index.get("items", []) if item.get("job_key") != job_key]
        items.insert(
            0,
            {
                "job_key": job_key,
                "title": title,
                "action": job.action,
                "export_hash": manifest.get("export_hash"),
                "notion_url": metadata["notion_url"],
                "drive_url": metadata["drive_url"],
                "markdown": version_md,
                "latest": latest_md,
                "archived_at": archived_at,
            },
        )
        index["items"] = items[:500]
        index["updated_at"] = archived_at
        self._write_text(repo_dir, index_path, json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        self._write_text(repo_dir, readme_path, self._readme(base_prefix))

        written = [version_md, latest_md, research_latest, manifest_path, index_path, readme_path]
        if settings.knowledge_github_archive_include_text:
            text_item = f"{version_dir}/{job_key}.txt"
            if (repo_dir / text_item).exists():
                written.append(text_item)
        return sorted(set(written))

    def _readme(self, base_prefix: str) -> str:
        root_note = "This repository is an automated, read-only mirror of selected Notion knowledge artifacts."
        if base_prefix:
            root_note += f"\n\nArchive root: `{base_prefix.rstrip('/')}`."
        return (
            "# Personal Knowledge Archive\n\n"
            f"{root_note}\n\n"
            "Notion remains the source of truth. Do not manually edit generated archive files unless you are intentionally repairing the mirror.\n\n"
            "## Structure\n\n"
            "- `research-packs/`: latest Markdown view per Research Pack.\n"
            "- `source-packs/`: versioned Source Pack exports by workspace and topic.\n"
            "- `manifests/`: sanitized export metadata, without API keys or local secrets.\n"
            "- `index.json`: latest archive index for agents and scripts.\n"
        )

    def _write_text(self, repo_dir: Path, relative_path: str, content: str) -> None:
        path = (repo_dir / relative_path).resolve()
        root = repo_dir.resolve()
        if not path.is_relative_to(root):
            raise RuntimeError(f"Archive path escapes repo root: {relative_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _read_json(self, path: Path, fallback: Any) -> Any:
        if not path.exists():
            return fallback
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return fallback

    def _git(self, repo_dir: Path, args: list[str], check: bool = True) -> str:
        env = dict(os.environ)
        key_file = settings.knowledge_github_archive_ssh_key_file
        if key_file:
            env["GIT_SSH_COMMAND"] = (
                f"ssh -i {shlex.quote(str(key_file))} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
            )
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if check and result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip()[:2000])
        return (result.stdout or result.stderr or "").strip()


class KnowledgeOrchestrator:
    def __init__(self) -> None:
        self.builder = ResearchPackBuilder()
        self.notion = NotionKnowledgeBridge()
        self.drive = DriveSourcePackSync()
        self.onyx = OnyxBridge()
        self.study_workspace = StudyWorkspaceBridge()
        self.github_archive = GitHubKnowledgeArchive()
        self.feishu = FeishuMessenger()

    def create_job(
        self,
        db: Session,
        command: ParsedKnowledgeCommand,
        request_text: str,
        source: str,
        requester: str | None = None,
        chat_id: str | None = None,
        source_event_id: str | None = None,
    ) -> KnowledgeJob:
        if not settings.knowledge_enabled:
            raise RuntimeError("Knowledge automation is disabled.")
        if source_event_id:
            existing = db.scalar(select(KnowledgeJob).where(KnowledgeJob.source_event_id == source_event_id))
            if existing:
                return existing
        now = datetime.utcnow()
        job = KnowledgeJob(
            job_key=f"KB{now:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}",
            action=command.action,
            status="queued",
            source=source,
            requester=requester,
            chat_id=chat_id,
            source_event_id=source_event_id,
            request_text=request_text,
            topic=command.topic,
            title=command.title,
            log_json=json_dump([{"at": now.isoformat(), "event": "queued"}]),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    def run_job(self, job_id: int) -> None:
        db = SessionLocal()
        try:
            job = db.get(KnowledgeJob, job_id)
            if not job:
                return
            self._mark(db, job, "running", "started")
            if job.action == "sync":
                result = self._run_sync(db, job)
            elif job.action == "add":
                result = self._run_add(db, job)
            else:
                result = self._run_research(db, job)
            job.status = "completed"
            job.finished_at = datetime.utcnow()
            job.result_json = json_dump(result)
            self._append_log(job, "completed", result)
            db.commit()
            self.feishu.send_text(self._format_done_message(job, result), chat_id=job.chat_id)
        except Exception as exc:
            db.rollback()
            job = db.get(KnowledgeJob, job_id)
            if job:
                job.status = "failed"
                job.error_text = str(exc)[:4000]
                job.finished_at = datetime.utcnow()
                self._append_log(job, "failed", {"error": job.error_text})
                db.commit()
                self.feishu.send_text(self._format_failed_message(job), chat_id=job.chat_id)
        finally:
            db.close()

    def _run_research(self, db: Session, job: KnowledgeJob) -> dict[str, Any]:
        if not job.topic:
            raise RuntimeError("Research topic is empty.")
        self._append_log(job, "building_research_pack_json")
        pack = self.builder.build(job.topic)
        job.title = pack["title"]
        input_path = self._write_research_input(job, pack)
        db.commit()

        self._append_log(job, "creating_notion_research_pack", {"input_json": str(input_path)})
        notion_result = self.notion.create_research_pack(input_path)
        research_pack = notion_result.get("research_pack") or notion_result
        job.notion_page_id = research_pack.get("id") or research_pack.get("page_id")
        job.notion_url = research_pack.get("url")
        db.commit()

        manifest = self._export_source_pack(job)
        drive_result = self._sync_drive(job, manifest)
        onyx_result = self._sync_onyx(job, manifest)
        study_result = self.study_workspace.build_result(manifest, drive_result)
        job.notebooklm_url = study_result.get("notebook_url") or study_result.get("space_url")
        db.commit()
        github_archive_result = self._sync_github_archive(job, manifest, drive_result, study_result)

        return {
            "input_json": str(input_path),
            "notion": notion_result,
            "source_pack": manifest,
            "drive": drive_result,
            "onyx": onyx_result,
            "study_workspace": study_result,
            "notebooklm": study_result,
            "github_archive": github_archive_result,
        }

    def _run_sync(self, db: Session, job: KnowledgeJob) -> dict[str, Any]:
        if not job.title and not job.topic:
            raise RuntimeError("Sync title is empty.")
        job.title = job.title or job.topic
        db.commit()
        manifest = self._export_source_pack(job)
        drive_result = self._sync_drive(job, manifest)
        onyx_result = self._sync_onyx(job, manifest)
        study_result = self.study_workspace.build_result(manifest, drive_result)
        job.notebooklm_url = study_result.get("notebook_url") or study_result.get("space_url")
        db.commit()
        github_archive_result = self._sync_github_archive(job, manifest, drive_result, study_result)
        return {
            "source_pack": manifest,
            "drive": drive_result,
            "onyx": onyx_result,
            "study_workspace": study_result,
            "notebooklm": study_result,
            "github_archive": github_archive_result,
        }

    def _run_add(self, db: Session, job: KnowledgeJob) -> dict[str, Any]:
        command = parse_knowledge_command(job.request_text)
        title = job.title or command.title or job.topic
        content = command.content or ""
        if not title:
            raise RuntimeError("Add title is empty.")
        if not content.strip():
            raise RuntimeError("Add content is empty. Use: kb add <Research Pack 标题> 换行 <新内容>")

        job.title = title
        self._append_log(job, "appending_to_notion_research_pack")
        append_result = self.notion.append_to_research_pack(
            title,
            content,
            source=job.source,
            requester=job.requester,
        )
        job.notion_page_id = append_result.get("id")
        job.notion_url = append_result.get("url")
        db.commit()

        manifest = self._export_source_pack(job)
        drive_result = self._sync_drive(job, manifest)
        onyx_result = self._sync_onyx(job, manifest)
        study_result = self.study_workspace.build_result(manifest, drive_result)
        job.notebooklm_url = study_result.get("notebook_url") or study_result.get("space_url")
        db.commit()
        github_archive_result = self._sync_github_archive(job, manifest, drive_result, study_result)
        return {
            "notion": append_result,
            "source_pack": manifest,
            "drive": drive_result,
            "onyx": onyx_result,
            "study_workspace": study_result,
            "notebooklm": study_result,
            "github_archive": github_archive_result,
        }

    def _write_research_input(self, job: KnowledgeJob, pack: dict[str, Any]) -> Path:
        settings.knowledge_research_inputs_dir.mkdir(parents=True, exist_ok=True)
        path = settings.knowledge_research_inputs_dir / f"{slugify(pack['title'])}-{job.job_key.lower()}.json"
        path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _export_source_pack(self, job: KnowledgeJob) -> dict[str, Any]:
        self._append_log(job, "exporting_study_source_pack")
        manifest = self.notion.export_study_source_pack(title=job.title, page_id=job.notion_page_id)
        job.source_pack_manifest_json = json_dump(manifest)
        return manifest

    def _sync_drive(self, job: KnowledgeJob, manifest: dict[str, Any]) -> dict[str, Any]:
        self._append_log(job, "syncing_drive")
        drive_result = self.drive.sync(manifest)
        job.drive_file_id = drive_result.get("file_id")
        job.drive_url = drive_result.get("url")
        return drive_result

    def _sync_onyx(self, job: KnowledgeJob, manifest: dict[str, Any]) -> dict[str, Any]:
        self._append_log(job, "notifying_onyx")
        onyx_result = self.onyx.notify(job, manifest)
        job.onyx_status = onyx_result.get("status")
        return onyx_result

    def _sync_github_archive(
        self,
        job: KnowledgeJob,
        manifest: dict[str, Any],
        drive_result: dict[str, Any],
        study_result: dict[str, Any],
    ) -> dict[str, Any]:
        self._append_log(job, "archiving_to_github")
        return self.github_archive.archive(job, manifest, drive_result, study_result)

    def _mark(self, db: Session, job: KnowledgeJob, status: str, event: str) -> None:
        job.status = status
        if status == "running" and not job.started_at:
            job.started_at = datetime.utcnow()
        self._append_log(job, event)
        db.commit()

    def _append_log(self, job: KnowledgeJob, event: str, detail: dict[str, Any] | None = None) -> None:
        logs = json_loads(job.log_json, [])
        logs.append({"at": datetime.utcnow().isoformat(), "event": event, "detail": detail or {}})
        job.log_json = json_dump(logs[-100:])

    def serialize_job(self, job: KnowledgeJob) -> dict[str, Any]:
        source_pack = json_loads(job.source_pack_manifest_json, None)
        return {
            "id": job.id,
            "job_key": job.job_key,
            "action": job.action,
            "status": job.status,
            "source": job.source,
            "requester": job.requester,
            "request_text": job.request_text,
            "topic": job.topic,
            "title": job.title,
            "notion_url": job.notion_url,
            "drive_url": job.drive_url,
            "notebooklm_url": job.notebooklm_url,
            "onyx_status": job.onyx_status,
            "source_pack": source_pack,
            "source_pack_downloads": self.source_pack_download_urls(job, source_pack),
            "result": json_loads(job.result_json, None),
            "error_text": job.error_text,
            "logs": json_loads(job.log_json, []),
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }

    def source_pack_download_urls(
        self,
        job: KnowledgeJob,
        manifest: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        base_url = (settings.knowledge_public_base_url or "").strip().rstrip("/")
        if not base_url:
            return {}
        manifest = manifest or json_loads(job.source_pack_manifest_json, None) or {}
        urls: dict[str, str] = {}
        for kind, key in (("md", "markdown_path"), ("txt", "text_path")):
            if manifest.get(key):
                token = sign_source_pack_token(job.job_key, kind)
                urls[kind] = (
                    f"{base_url}/api/knowledge/jobs/{quote(job.job_key)}/source-pack/{kind}"
                    f"?token={quote(token)}"
                )
        return urls

    def _format_done_message(self, job: KnowledgeJob, result: dict[str, Any]) -> str:
        drive = result.get("drive") or {}
        github_archive = result.get("github_archive") or {}
        study_workspace = result.get("study_workspace") or result.get("notebooklm") or {}
        workspace_label = settings.knowledge_study_workspace_label or settings.knowledge_study_workspace
        lines = [
            f"知识库任务完成：{job.job_key}",
            f"主题：{job.title or job.topic or '-'}",
        ]
        if job.notion_url:
            lines.append(f"Notion：{job.notion_url}")
        if drive.get("url"):
            lines.append(f"Drive Source Pack：{drive['url']}")
        elif (result.get("source_pack") or {}).get("text_path"):
            lines.append(f"Source Pack：{(result.get('source_pack') or {}).get('text_path')}")
        downloads = self.source_pack_download_urls(job, result.get("source_pack") or {})
        if downloads.get("md"):
            lines.append(f"Markdown 下载：{downloads['md']}")
        if downloads.get("txt"):
            lines.append(f"Text 下载：{downloads['txt']}")
        if study_workspace.get("workspace_url"):
            lines.append(f"{workspace_label}：{study_workspace['workspace_url']}")
        if github_archive.get("status") == "archived":
            lines.append(f"GitHub Archive：{github_archive.get('commit')}")
        elif github_archive.get("status") and github_archive.get("status") != "skipped_disabled":
            lines.append(f"GitHub Archive：{github_archive.get('status')}")
        if (settings.knowledge_study_workspace or "").lower() == "notebooklm" and not drive.get("url"):
            lines.append("下一步：本机 NotebookLM 同步器会在你的电脑在线时下载 Source Pack，并通过本机浏览器/NotebookLM 登录态处理上传。")
        elif (settings.knowledge_study_workspace or "").lower() == "notebooklm" and drive.get("url"):
            lines.append("下一步：把 Drive Source Pack 作为来源加入对应 NotebookLM 专题；之后 Hermes 更新 Drive 文档即可同步。")
        else:
            lines.append(f"下一步：打开 {workspace_label}，把 Source Pack 作为文件或复制文本加入专题；自动化主链路已回写 Notion，并可继续由飞书触发同步。")
        return "\n".join(lines)

    def _format_failed_message(self, job: KnowledgeJob) -> str:
        return "\n".join(
            [
                f"知识库任务失败：{job.job_key}",
                f"主题：{job.title or job.topic or '-'}",
                f"错误：{job.error_text or '-'}",
            ]
        )


orchestrator = KnowledgeOrchestrator()
