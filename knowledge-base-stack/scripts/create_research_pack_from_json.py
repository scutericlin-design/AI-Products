#!/usr/bin/env python3
"""Create a Notion Research Pack and related knowledge-base rows from JSON."""

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


def make_text_block(block_type: str, text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    blocks = []
    remaining = text
    while remaining:
        part = remaining[:1900]
        remaining = remaining[1900:]
        blocks.append(
            {
                "object": "block",
                "type": block_type,
                block_type: {"rich_text": rich_text(part)},
            }
        )
    return blocks


def make_blocks(items: list[dict[str, str]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in items:
        block_type = item.get("type", "paragraph")
        text = item.get("text", "")
        if block_type in {"heading_1", "heading_2", "heading_3"}:
            output.extend(make_text_block(block_type, text[:1900]))
        elif block_type in {"paragraph", "bulleted_list_item", "numbered_list_item", "quote", "callout"}:
            output.extend(make_text_block(block_type, text))
        elif block_type == "divider":
            output.append({"object": "block", "type": "divider", "divider": {}})
        else:
            output.extend(make_text_block("paragraph", text))
    return output


def query_by_title(
    token: str,
    db_id: str,
    title: str,
    notion_version: str,
) -> list[dict[str, Any]]:
    payload = {
        "filter": {"property": "Name", "title": {"equals": title}},
        "page_size": 10,
    }
    return request(token, "POST", f"/databases/{db_id}/query", payload, notion_version).get("results", [])


def create_page(
    token: str,
    database_id_value: str,
    properties: dict[str, Any],
    children: list[dict[str, Any]] | None,
    notion_version: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "parent": {"type": "database_id", "database_id": database_id_value},
        "properties": properties,
    }
    if children:
        payload["children"] = children[:100]
    return request(token, "POST", "/pages", payload, notion_version)


def text_prop(value: str) -> dict[str, Any]:
    return {"rich_text": rich_text(value)}


def title_prop(value: str) -> dict[str, Any]:
    return {"title": title_text(value)}


def select_prop(value: str | None) -> dict[str, Any] | None:
    return {"select": {"name": value}} if value else None


def multi_select_prop(values: list[str]) -> dict[str, Any]:
    return {"multi_select": [{"name": item} for item in values if item]}


def date_prop(value: str | None) -> dict[str, Any] | None:
    return {"date": {"start": value}} if value else None


def drop_none(properties: dict[str, Any | None]) -> dict[str, Any]:
    return {key: value for key, value in properties.items() if value is not None}


def research_pack_properties(data: dict[str, Any]) -> dict[str, Any]:
    return drop_none(
        {
            "Name": title_prop(data["title"]),
            "Question": text_prop(data.get("question", "")),
            "Status": select_prop(data.get("status", "Synthesizing")),
            "Topic": multi_select_prop(data.get("topic", [])),
            "Priority": select_prop(data.get("priority", "Medium")),
            "NotebookLM URL": {"url": data.get("notebooklm_url") or None},
            "Onyx Agent": select_prop(data.get("onyx_agent")),
            "Source Count": {"number": data.get("source_count")},
            "Key Takeaways": text_prop(data.get("key_takeaways", "")),
            "Decision": text_prop(data.get("decision", "")),
            "Next Action": text_prop(data.get("next_action", "")),
            "AI Index": {"checkbox": bool(data.get("ai_index", False))},
            "Created": date_prop(data.get("created")),
            "Review Date": date_prop(data.get("review_date")),
        }
    )


def source_properties(source: dict[str, Any], title: str) -> dict[str, Any]:
    return drop_none(
        {
            "Name": title_prop(source["name"]),
            "Type": select_prop(source.get("type", "Article")),
            "URL": {"url": source.get("url") or None},
            "Author": text_prop(source.get("author", "")),
            "Published Date": date_prop(source.get("published_date")),
            "Topic": multi_select_prop(source.get("topic", [])),
            "Added For": text_prop(title),
            "Quality": select_prop(source.get("quality", "Unknown")),
            "Used In Research Pack": {"checkbox": True},
            "AI Index": {"checkbox": bool(source.get("ai_index", False))},
            "Notes": text_prop(source.get("notes", "")),
        }
    )


def evergreen_properties(note: dict[str, Any], title: str) -> dict[str, Any]:
    return drop_none(
        {
            "Name": title_prop(note["name"]),
            "Type": select_prop(note.get("type", "Insight")),
            "Topic": multi_select_prop(note.get("topic", [])),
            "Status": select_prop(note.get("status", "Stable")),
            "Confidence": select_prop(note.get("confidence", "Medium")),
            "Source URL": {"url": note.get("source_url") or None},
            "Related Research Pack": text_prop(title),
            "AI Index": {"checkbox": bool(note.get("ai_index", True))},
            "Summary": text_prop(note.get("summary", "")),
        }
    )


def decision_properties(decision: dict[str, Any], title: str) -> dict[str, Any]:
    return drop_none(
        {
            "Name": title_prop(decision["name"]),
            "Area": select_prop(decision.get("area", "Knowledge")),
            "Decision": text_prop(decision.get("decision", "")),
            "Rationale": text_prop(decision.get("rationale", "")),
            "Alternatives": text_prop(decision.get("alternatives", "")),
            "Revisit Date": date_prop(decision.get("revisit_date")),
            "Related Research Pack": text_prop(title),
            "AI Index": {"checkbox": bool(decision.get("ai_index", True))},
        }
    )


def ai_output_properties(output: dict[str, Any], title: str) -> dict[str, Any]:
    return drop_none(
        {
            "Name": title_prop(output["name"]),
            "Type": select_prop(output.get("type", "Memo")),
            "Source Tool": select_prop(output.get("source_tool", "ChatGPT")),
            "Related Research Pack": text_prop(title),
            "Summary": text_prop(output.get("summary", "")),
            "URL": {"url": output.get("url") or None},
            "Created": date_prop(output.get("created")),
            "AI Index": {"checkbox": bool(output.get("ai_index", True))},
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", default=None)
    parser.add_argument("--token-file", default=os.getenv("NOTION_API_KEY_FILE"))
    parser.add_argument("--input-json", required=True)
    parser.add_argument(
        "--setup-json",
        default=str(Path(__file__).resolve().parents[1] / "notion-setup-result.json"),
    )
    parser.add_argument("--notion-version", default=os.getenv("NOTION_VERSION", DEFAULT_NOTION_VERSION))
    parser.add_argument("--allow-duplicate", action="store_true")
    args = parser.parse_args()

    token = load_token(args)
    setup = json.loads(Path(args.setup_json).read_text(encoding="utf-8"))
    data = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    title = data["title"]

    research_db = database_id(setup, "Research Packs")
    existing = query_by_title(token, research_db, title, args.notion_version)
    if existing and not args.allow_duplicate:
        print(
            json.dumps(
                {
                    "status": "exists",
                    "title": title,
                    "page_id": existing[0]["id"],
                    "url": existing[0].get("url"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    research_page = create_page(
        token,
        research_db,
        research_pack_properties(data),
        make_blocks(data.get("body", [])),
        args.notion_version,
    )

    created: dict[str, Any] = {
        "research_pack": {
            "id": research_page["id"],
            "url": research_page.get("url"),
            "title": title,
        },
        "sources": [],
        "evergreen_notes": [],
        "decisions": [],
        "ai_outputs": [],
    }

    for source in data.get("sources", []):
        page = create_page(
            token,
            database_id(setup, "Source Library"),
            source_properties(source, title),
            make_blocks(source.get("body", [])),
            args.notion_version,
        )
        created["sources"].append({"id": page["id"], "url": page.get("url"), "name": source["name"]})

    for note in data.get("evergreen_notes", []):
        page = create_page(
            token,
            database_id(setup, "Evergreen Notes"),
            evergreen_properties(note, title),
            make_blocks(note.get("body", [])),
            args.notion_version,
        )
        created["evergreen_notes"].append({"id": page["id"], "url": page.get("url"), "name": note["name"]})

    for decision in data.get("decisions", []):
        page = create_page(
            token,
            database_id(setup, "Decisions"),
            decision_properties(decision, title),
            make_blocks(decision.get("body", [])),
            args.notion_version,
        )
        created["decisions"].append({"id": page["id"], "url": page.get("url"), "name": decision["name"]})

    for output in data.get("ai_outputs", []):
        page = create_page(
            token,
            database_id(setup, "AI Outputs"),
            ai_output_properties(output, title),
            make_blocks(output.get("body", [])),
            args.notion_version,
        )
        created["ai_outputs"].append({"id": page["id"], "url": page.get("url"), "name": output["name"]})

    print(json.dumps(created, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
