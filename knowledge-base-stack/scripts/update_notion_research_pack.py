#!/usr/bin/env python3
"""Update a Research Pack row in Notion."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


NOTION_API_BASE = "https://api.notion.com/v1"
DEFAULT_NOTION_VERSION = "2022-06-28"


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
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notion API error {exc.code}: {body}") from exc


def find_page(token: str, database_id: str, title: str) -> str:
    payload = {
        "filter": {
            "property": "Name",
            "title": {
                "equals": title,
            },
        },
        "page_size": 1,
    }
    result = request(token, "POST", f"/databases/{database_id}/query", payload)
    rows = result.get("results", [])
    if not rows:
        raise RuntimeError(f"No Research Pack found with title: {title}")
    return rows[0]["id"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", default=os.getenv("NOTION_API_KEY"))
    parser.add_argument("--token-file", default=os.getenv("NOTION_API_KEY_FILE"))
    parser.add_argument("--database-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--notebooklm-url", required=True)
    parser.add_argument("--status")
    parser.add_argument("--notebooklm-sync-status")
    parser.add_argument("--notebooklm-source-pack-path")
    parser.add_argument("--notebooklm-last-exported", action="store_true")
    args = parser.parse_args()

    token = args.token
    if not token and args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    if not token:
        print("Provide --token, --token-file, or NOTION_API_KEY.", file=sys.stderr)
        return 2

    page_id = find_page(token, args.database_id, args.title)
    properties: dict[str, Any] = {
        "NotebookLM URL": {"url": args.notebooklm_url},
    }
    if args.status:
        properties["Status"] = {"select": {"name": args.status}}
    if args.notebooklm_sync_status:
        properties["NotebookLM Sync Status"] = {
            "select": {"name": args.notebooklm_sync_status}
        }
    if args.notebooklm_source_pack_path:
        properties["NotebookLM Source Pack Path"] = {
            "rich_text": [
                {"type": "text", "text": {"content": args.notebooklm_source_pack_path}}
            ]
        }
    if args.notebooklm_last_exported:
        properties["NotebookLM Last Exported"] = {
            "date": {"start": datetime.now(timezone.utc).isoformat()}
        }

    updated = request(token, "PATCH", f"/pages/{page_id}", {"properties": properties})
    print(json.dumps({"page_id": updated["id"], "url": updated.get("url")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
