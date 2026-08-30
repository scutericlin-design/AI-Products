from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _env_file_value(path: str, key: str) -> str:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == key:
            return v.strip().strip('"').strip("'")
    return ""


def _platform_value(event: Any) -> str:
    source = getattr(event, "source", None)
    platform = getattr(source, "platform", "")
    return str(getattr(platform, "value", platform) or "").lower()


def _event_text(event: Any) -> str:
    return str(getattr(event, "text", "") or "").strip()


def _is_kb_command(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered == "kb" or lowered.startswith("kb ") or lowered == "/kb" or lowered.startswith("/kb ")


def _knowledge_secret() -> str:
    return (
        os.getenv("HERMES_KB_COMMAND_SECRET")
        or os.getenv("KNOWLEDGE_COMMAND_SECRET")
        or _env_file_value("/opt/chixiao-alpha/.env", "KNOWLEDGE_COMMAND_SECRET")
    )


def _knowledge_url() -> str:
    return os.getenv("HERMES_KB_FEISHU_EVENTS_URL") or "http://127.0.0.1/api/feishu/knowledge/events"


def _feishu_credentials() -> tuple[str, str]:
    app_id = os.getenv("FEISHU_APP_ID") or _env_file_value("/root/.hermes/.env", "FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET") or _env_file_value("/root/.hermes/.env", "FEISHU_APP_SECRET")
    return app_id, app_secret


def _feishu_token() -> str:
    app_id, app_secret = _feishu_credentials()
    if not app_id or not app_secret:
        return ""
    payload = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    req = Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return str(data.get("tenant_access_token") or "")


def _send_feishu_text(chat_id: str, text: str) -> None:
    if not chat_id or not text:
        return
    token = _feishu_token()
    if not token:
        return
    payload = json.dumps(
        {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=12) as resp:
        resp.read()


def _build_feishu_event(event: Any, text: str) -> dict[str, Any]:
    source = getattr(event, "source", None)
    chat_id = str(getattr(source, "chat_id", "") or "")
    user_id = str(getattr(source, "user_id", "") or "")
    message_id = str(getattr(source, "message_id", "") or getattr(event, "message_id", "") or "")
    if not message_id:
        digest = hashlib.sha256(f"{chat_id}\n{user_id}\n{text}\n{time.time()}".encode("utf-8")).hexdigest()[:24]
        message_id = f"hermes-kb-{digest}"
    return {
        "header": {"event_id": message_id},
        "event": {
            "sender": {"sender_id": {"open_id": user_id}},
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        },
    }


def _post_to_knowledge(event: Any, text: str) -> None:
    secret = _knowledge_secret()
    if not secret:
        raise RuntimeError("KNOWLEDGE_COMMAND_SECRET is not configured")
    url = _knowledge_url()
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}{urlencode({'secret': secret})}"
    payload = json.dumps(_build_feishu_event(event, text), ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "X-Knowledge-Secret": secret},
        method="POST",
    )
    with urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status >= 400:
            raise RuntimeError(body[:500])


def _pre_gateway_dispatch(event: Any = None, **_: Any) -> dict[str, str] | None:
    if event is None or _platform_value(event) != "feishu":
        return None
    text = _event_text(event)
    if not _is_kb_command(text):
        return None

    source = getattr(event, "source", None)
    chat_id = str(getattr(source, "chat_id", "") or "")
    try:
        _post_to_knowledge(event, text)
    except Exception as exc:
        try:
            _send_feishu_text(chat_id, f"知识库命令转发失败：{str(exc)[:500]}")
        except Exception:
            pass
    return {"action": "skip", "reason": "knowledge_kb_router"}


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
