#!/usr/bin/env python3
"""Create an AI Outputs row in the Notion knowledge base."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any


NOTION_API_BASE = "https://api.notion.com/v1"
DEFAULT_NOTION_VERSION = "2022-06-28"


def rich_text(content: str) -> list[dict[str, Any]]:
    if not content:
        return []
    chunks = []
    remaining = content
    while remaining:
        part = remaining[:1900]
        remaining = remaining[1900:]
        chunks.append({"type": "text", "text": {"content": part}})
    return chunks


def title_text(content: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": content or "Untitled"}}]


def paragraph(content: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": rich_text(content)},
    }


def heading(content: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": rich_text(content)},
    }


def load_token(args: argparse.Namespace) -> str:
    token = args.token or os.getenv("NOTION_API_KEY")
    if not token and args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("Provide --token, --token-file, or NOTION_API_KEY.")
    return token


def request(
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    notion_version: str = DEFAULT_NOTION_VERSION,
) -> dict[str, Any]:
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": notion_version,
        "Content-Type": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        f"{NOTION_API_BASE}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            time.sleep(0.35)
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notion API error {exc.code} for {method} {path}: {body}") from exc


def database_id(setup: dict[str, Any], name: str) -> str:
    try:
        return setup["databases"][name]["id"]
    except KeyError as exc:
        raise RuntimeError(f"Database not found in setup json: {name}") from exc


def blocks_from_text(text: str) -> list[dict[str, Any]]:
    blocks = [heading("NotebookLM Output")]
    paragraphs = [item.strip() for item in text.split("\n\n") if item.strip()]
    if not paragraphs and text.strip():
        paragraphs = [text.strip()]
    for item in paragraphs:
        remaining = item
        while remaining:
            part = remaining[:1900]
            remaining = remaining[1900:]
            blocks.append(paragraph(part))
    return blocks[:100]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", default=None)
    parser.add_argument("--token-file", default=os.getenv("NOTION_API_KEY_FILE"))
    parser.add_argument(
        "--setup-json",
        default=str(Path(__file__).resolve().parents[1] / "notion-setup-result.json"),
    )
    parser.add_argument("--notion-version", default=os.getenv("NOTION_VERSION", DEFAULT_NOTION_VERSION))
    parser.add_argument("--name", required=True)
    parser.add_argument("--type", default="Memo")
    parser.add_argument("--source-tool", default="NotebookLM")
    parser.add_argument("--related-research-pack", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--url")
    parser.add_argument("--body-file")
    parser.add_argument("--created", default=date.today().isoformat())
    parser.add_argument("--no-ai-index", action="store_true")
    args = parser.parse_args()

    token = load_token(args)
    setup = json.loads(Path(args.setup_json).read_text(encoding="utf-8"))
    body_text = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else ""
    payload = {
        "parent": {"type": "database_id", "database_id": database_id(setup, "AI Outputs")},
        "properties": {
            "Name": {"title": title_text(args.name)},
            "Type": {"select": {"name": args.type}},
            "Source Tool": {"select": {"name": args.source_tool}},
            "Related Research Pack": {"rich_text": rich_text(args.related_research_pack)},
            "Summary": {"rich_text": rich_text(args.summary)},
            "URL": {"url": args.url or None},
            "Created": {"date": {"start": args.created}},
            "AI Index": {"checkbox": not args.no_ai_index},
        },
    }
    if body_text.strip():
        payload["children"] = blocks_from_text(body_text)

    created = request(token, "POST", "/pages", payload, args.notion_version)
    print(json.dumps({"id": created["id"], "url": created.get("url")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
