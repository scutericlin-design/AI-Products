#!/usr/bin/env python3
"""Add NotebookLM sync fields to the Notion knowledge-base databases.

This script uses only the Python standard library. It is intentionally safe to
re-run: Notion treats property names in PATCH /databases as upserts.

Required:
  NOTION_API_KEY or --token/--token-file

Optional:
  --setup-json defaults to ../notion-setup-result.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
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
            time.sleep(0.35)
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notion API error {exc.code} for {method} {path}: {body}") from exc


def select_schema(options: list[str]) -> dict[str, Any]:
    colors = ["gray", "yellow", "blue", "green", "red", "purple"]
    return {
        "select": {
            "options": [
                {"name": name, "color": colors[index % len(colors)]}
                for index, name in enumerate(options)
            ]
        }
    }


def load_token(args: argparse.Namespace) -> str:
    token = args.token
    if not token and args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    if not token:
        token = os.getenv("NOTION_API_KEY")
    if not token:
        raise RuntimeError("Provide --token, --token-file, or NOTION_API_KEY.")
    return token


def database_id(setup: dict[str, Any], name: str) -> str:
    try:
        return setup["databases"][name]["id"]
    except KeyError as exc:
        raise RuntimeError(f"Database not found in setup json: {name}") from exc


def patch_database(
    token: str,
    db_id: str,
    properties: dict[str, Any],
    notion_version: str,
    dry_run: bool,
) -> dict[str, Any]:
    payload = {"properties": properties}
    if dry_run:
        return {"id": db_id, "dry_run": True, "payload": payload}
    return request(token, "PATCH", f"/databases/{db_id}", payload, notion_version)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", default=None)
    parser.add_argument("--token-file", default=os.getenv("NOTION_API_KEY_FILE"))
    parser.add_argument(
        "--setup-json",
        default=str(Path(__file__).resolve().parents[1] / "notion-setup-result.json"),
    )
    parser.add_argument("--notion-version", default=os.getenv("NOTION_VERSION", DEFAULT_NOTION_VERSION))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = "dry-run-token" if args.dry_run else load_token(args)
    setup = json.loads(Path(args.setup_json).read_text(encoding="utf-8"))

    sync_status = select_schema(["Pending", "Exported", "Added", "Skipped", "Error"])

    updates = {
        "Research Packs": {
            "NotebookLM Sync": {"checkbox": {}},
            "NotebookLM Sync Status": sync_status,
            "NotebookLM Source Pack Path": {"rich_text": {}},
            "NotebookLM Last Exported": {"date": {}},
            "NotebookLM Export Hash": {"rich_text": {}},
        },
        "Source Library": {
            "NotebookLM Sync": {"checkbox": {}},
            "NotebookLM Sync Status": sync_status,
            "NotebookLM Last Exported": {"date": {}},
        },
        "Evergreen Notes": {
            "NotebookLM Sync": {"checkbox": {}},
            "NotebookLM Sync Status": sync_status,
            "NotebookLM Last Exported": {"date": {}},
        },
    }

    result: dict[str, Any] = {}
    for name, properties in updates.items():
        db_id = database_id(setup, name)
        updated = patch_database(token, db_id, properties, args.notion_version, args.dry_run)
        result[name] = {
            "database_id": updated["id"],
            "properties_added": sorted(properties.keys()),
        }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
