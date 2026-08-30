#!/usr/bin/env python3
"""Create the Notion knowledge-base stack from local templates.

This script intentionally uses only the Python standard library. It expects a
temporary Notion internal integration with insert/update/read content access and
a parent page shared with that integration.

Required env vars or CLI flags:
  NOTION_API_KEY
  NOTION_PARENT_PAGE_ID or NOTION_PARENT_PAGE_URL
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_NOTION_VERSION = "2022-06-28"
NOTION_API_BASE = "https://api.notion.com/v1"


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


def block(block_type: str, content: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": rich_text(content)},
    }


def paragraph(content: str) -> dict[str, Any]:
    return block("paragraph", content)


def heading(level: int, content: str) -> dict[str, Any]:
    return block(f"heading_{level}", content)


def bullet(content: str) -> dict[str, Any]:
    return block("bulleted_list_item", content)


def extract_page_id(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("Missing Notion parent page id or URL.")
    compact = value.replace("-", "")
    if re.fullmatch(r"[0-9a-fA-F]{32}", compact):
        return hyphenate(compact)

    no_query = re.split(r"[?#]", value, maxsplit=1)[0]
    matches = re.findall(r"[0-9a-fA-F]{32}", no_query)
    if not matches:
        raise ValueError(
            "Could not find a 32-character Notion page id in the provided value."
        )
    return hyphenate(matches[-1])


def hyphenate(compact_id: str) -> str:
    compact = compact_id.replace("-", "")
    return (
        f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-"
        f"{compact[16:20]}-{compact[20:32]}"
    )


class NotionClient:
    def __init__(
        self,
        token: str,
        version: str,
        dry_run: bool = False,
        pause_seconds: float = 0.35,
    ) -> None:
        self.token = token
        self.version = version
        self.dry_run = dry_run
        self.pause_seconds = pause_seconds
        self.counter = 0

    def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.counter += 1
        label = f"dry-run-{self.counter:03d}"
        if self.dry_run:
            print(f"[DRY RUN] {method} {path}")
            if payload:
                print(json.dumps(payload, ensure_ascii=False, indent=2)[:1200])
            return {"id": label, "url": f"notion://{label}"}

        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.version,
            "Content-Type": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        request = urllib.request.Request(
            f"{NOTION_API_BASE}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
                time.sleep(self.pause_seconds)
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Notion API error {exc.code} for {method} {path}: {body}"
            ) from exc

    def create_child_page(
        self,
        parent_page_id: str,
        title: str,
        children: list[dict[str, Any]] | None = None,
        emoji: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "properties": {"title": {"title": title_text(title)}},
        }
        if children:
            payload["children"] = children
        if emoji:
            payload["icon"] = {"type": "emoji", "emoji": emoji}
        return self.request("POST", "/pages", payload)

    def create_database(
        self,
        parent_page_id: str,
        title: str,
        properties: dict[str, Any],
        emoji: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": title_text(title),
            "properties": properties,
            "is_inline": False,
        }
        if emoji:
            payload["icon"] = {"type": "emoji", "emoji": emoji}
        return self.request("POST", "/databases", payload)

    def create_database_page(
        self,
        database_id: str,
        properties: dict[str, Any],
        children: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "parent": {"type": "database_id", "database_id": database_id},
            "properties": properties,
        }
        if children:
            payload["children"] = children
        return self.request("POST", "/pages", payload)


def select_schema(options: list[str]) -> dict[str, Any]:
    colors = [
        "gray",
        "brown",
        "orange",
        "yellow",
        "green",
        "blue",
        "purple",
        "pink",
        "red",
    ]
    return {
        "select": {
            "options": [
                {"name": name, "color": colors[index % len(colors)]}
                for index, name in enumerate(options)
            ]
        }
    }


def multi_select_schema(options: list[str]) -> dict[str, Any]:
    colors = [
        "blue",
        "green",
        "purple",
        "orange",
        "pink",
        "red",
        "yellow",
        "gray",
    ]
    return {
        "multi_select": {
            "options": [
                {"name": name, "color": colors[index % len(colors)]}
                for index, name in enumerate(options)
            ]
        }
    }


def database_schemas() -> dict[str, dict[str, Any]]:
    topics = [
        "Knowledge Base",
        "AI Agent",
        "Product",
        "Trading",
        "Writing",
        "Personal Ops",
    ]
    return {
        "Research Packs": {
            "Name": {"title": {}},
            "Question": {"rich_text": {}},
            "Status": select_schema(
                ["Inbox", "Researching", "Synthesizing", "Done", "Archived"]
            ),
            "Topic": multi_select_schema(topics),
            "Priority": select_schema(["High", "Medium", "Low"]),
            "NotebookLM URL": {"url": {}},
            "Onyx Agent": select_schema(
                [
                    "Personal Knowledge Recall",
                    "Product Research Synthesizer",
                    "Project Navigator",
                    "Weekly Knowledge Curator",
                ]
            ),
            "Source Count": {"number": {"format": "number"}},
            "Key Takeaways": {"rich_text": {}},
            "Decision": {"rich_text": {}},
            "Next Action": {"rich_text": {}},
            "AI Index": {"checkbox": {}},
            "Created": {"date": {}},
            "Review Date": {"date": {}},
        },
        "Source Library": {
            "Name": {"title": {}},
            "Type": select_schema(
                ["Article", "PDF", "Video", "Podcast", "Book", "Report", "Doc"]
            ),
            "URL": {"url": {}},
            "Author": {"rich_text": {}},
            "Published Date": {"date": {}},
            "Topic": multi_select_schema(topics),
            "Added For": {"rich_text": {}},
            "Quality": select_schema(["A", "B", "C", "Unknown"]),
            "Used In Research Pack": {"checkbox": {}},
            "AI Index": {"checkbox": {}},
            "Notes": {"rich_text": {}},
        },
        "Evergreen Notes": {
            "Name": {"title": {}},
            "Type": select_schema(
                ["Principle", "Framework", "Insight", "Playbook", "Definition"]
            ),
            "Topic": multi_select_schema(topics),
            "Status": select_schema(["Draft", "Stable", "Needs Review"]),
            "Confidence": select_schema(["High", "Medium", "Low"]),
            "Source URL": {"url": {}},
            "Related Research Pack": {"rich_text": {}},
            "AI Index": {"checkbox": {}},
            "Summary": {"rich_text": {}},
        },
        "Decisions": {
            "Name": {"title": {}},
            "Area": select_schema(
                ["Knowledge", "Product", "Investment", "Ops", "Personal"]
            ),
            "Decision": {"rich_text": {}},
            "Rationale": {"rich_text": {}},
            "Alternatives": {"rich_text": {}},
            "Revisit Date": {"date": {}},
            "Related Research Pack": {"rich_text": {}},
            "AI Index": {"checkbox": {}},
        },
        "Projects": {
            "Name": {"title": {}},
            "Goal": {"rich_text": {}},
            "Status": select_schema(["Planned", "Active", "Waiting", "Done", "Archived"]),
            "Related Research Packs": {"rich_text": {}},
            "Related Decisions": {"rich_text": {}},
            "Next Action": {"rich_text": {}},
            "Review Date": {"date": {}},
            "AI Index": {"checkbox": {}},
        },
        "AI Outputs": {
            "Name": {"title": {}},
            "Type": select_schema(["Summary", "Memo", "Draft", "FAQ", "Audio Notes"]),
            "Source Tool": select_schema(["Onyx", "NotebookLM", "ChatGPT", "Other"]),
            "Related Research Pack": {"rich_text": {}},
            "Summary": {"rich_text": {}},
            "URL": {"url": {}},
            "Created": {"date": {}},
            "AI Index": {"checkbox": {}},
        },
        "SOP / Templates": {
            "Name": {"title": {}},
            "Type": select_schema(["SOP", "Prompt", "Template", "Checklist"]),
            "Trigger": {"rich_text": {}},
            "Steps": {"rich_text": {}},
            "Owner": {"rich_text": {}},
            "AI Index": {"checkbox": {}},
        },
    }


def database_icons() -> dict[str, str]:
    return {
        "Research Packs": "🧭",
        "Source Library": "📚",
        "Evergreen Notes": "🌱",
        "Decisions": "✅",
        "Projects": "🚀",
        "AI Outputs": "✨",
        "SOP / Templates": "🧰",
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def split_multi(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[;,]", value or "") if item.strip()]


def csv_value_to_property(prop_schema: dict[str, Any], value: str) -> dict[str, Any] | None:
    value = (value or "").strip()
    prop_type = next(iter(prop_schema.keys()))

    if prop_type == "title":
        return {"title": title_text(value)}
    if not value and prop_type not in {"checkbox", "number"}:
        return None
    if prop_type == "rich_text":
        return {"rich_text": rich_text(value)}
    if prop_type == "select":
        return {"select": {"name": value}}
    if prop_type == "multi_select":
        return {"multi_select": [{"name": item} for item in split_multi(value)]}
    if prop_type == "url":
        return {"url": value or None}
    if prop_type == "number":
        if not value:
            return {"number": None}
        try:
            return {"number": int(value)}
        except ValueError:
            return {"number": float(value)}
    if prop_type == "checkbox":
        return {"checkbox": value.lower() in {"true", "yes", "1", "y"}}
    if prop_type == "date":
        return {"date": {"start": value}} if value else None
    return None


def row_to_properties(schema: dict[str, Any], row: dict[str, str]) -> dict[str, Any]:
    properties = {}
    for name, prop_schema in schema.items():
        if name not in row:
            continue
        converted = csv_value_to_property(prop_schema, row[name])
        if converted is not None:
            properties[name] = converted
    return properties


def research_pack_children(row: dict[str, str]) -> list[dict[str, Any]]:
    return [
        heading(2, "Research Question"),
        paragraph(row.get("Question", "")),
        heading(2, "Context"),
        paragraph("为什么现在要研究这个问题？已有判断是什么？"),
        heading(2, "Sources"),
        bullet("Source 1:"),
        bullet("Source 2:"),
        bullet("Source 3:"),
        heading(2, "NotebookLM Work"),
        bullet(f"NotebookLM URL: {row.get('NotebookLM URL', '')}"),
        bullet("生成过的内容: Summary / FAQ / Audio / Mind Map / Slides"),
        heading(2, "Key Takeaways"),
        paragraph(row.get("Key Takeaways", "")),
        heading(2, "Decision"),
        paragraph(row.get("Decision", "")),
        heading(2, "Follow-up"),
        bullet(f"下一步行动: {row.get('Next Action', '')}"),
        bullet("需要进入 Evergreen Notes 的观点:"),
        bullet("需要进入 Projects 的事项:"),
    ]


def evergreen_children(row: dict[str, str]) -> list[dict[str, Any]]:
    return [
        heading(2, "Summary"),
        paragraph(row.get("Summary", "")),
        heading(2, "Evidence"),
        paragraph("补充主要来源、反例和置信度。"),
        heading(2, "Reuse"),
        paragraph("未来在哪些场景可以复用这条知识？"),
    ]


def create_sample_rows(
    client: NotionClient,
    database_ids: dict[str, str],
    schemas: dict[str, dict[str, Any]],
    template_dir: Path,
) -> None:
    csv_files = {
        "Research Packs": "research-packs.csv",
        "Source Library": "source-library.csv",
        "Evergreen Notes": "evergreen-notes.csv",
    }
    for database_name, filename in csv_files.items():
        rows = read_csv(template_dir / filename)
        for row in rows:
            children = None
            if database_name == "Research Packs":
                children = research_pack_children(row)
            elif database_name == "Evergreen Notes":
                children = evergreen_children(row)
            client.create_database_page(
                database_ids[database_name],
                row_to_properties(schemas[database_name], row),
                children=children,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-page-id", default=os.getenv("NOTION_PARENT_PAGE_ID"))
    parser.add_argument("--parent-page-url", default=os.getenv("NOTION_PARENT_PAGE_URL"))
    parser.add_argument("--token", default=os.getenv("NOTION_API_KEY"))
    parser.add_argument("--token-file", default=os.getenv("NOTION_API_KEY_FILE"))
    parser.add_argument("--title", default="AI Knowledge Hub")
    parser.add_argument("--notion-version", default=os.getenv("NOTION_VERSION", DEFAULT_NOTION_VERSION))
    parser.add_argument("--template-dir", default=str(Path(__file__).resolve().parents[1] / "templates"))
    parser.add_argument("--output", default=str(Path(__file__).resolve().parents[1] / "notion-setup-result.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-samples", action="store_true")
    args = parser.parse_args()

    parent_raw = args.parent_page_id or args.parent_page_url
    if not parent_raw:
        print("Provide --parent-page-id/--parent-page-url or NOTION_PARENT_PAGE_ID/URL.", file=sys.stderr)
        return 2
    token = args.token
    if not token and args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()

    if not token and not args.dry_run:
        print("Provide --token or NOTION_API_KEY, or run with --dry-run.", file=sys.stderr)
        return 2

    parent_page_id = extract_page_id(parent_raw)
    client = NotionClient(
        token=token or "dry-run-token",
        version=args.notion_version,
        dry_run=args.dry_run,
    )

    hub_children = [
        paragraph("这是个人知识库的 AI 索引根页面。Notion 负责长期沉淀，Onyx 负责检索和 Agent，NotebookLM 负责专题研究。"),
        heading(2, "Operating Rules"),
        bullet("只把值得长期复用的内容勾选 AI Index。"),
        bullet("NotebookLM 的研究结果必须回写到 Research Packs。"),
        bullet("Onyx 只连接这个页面，不直接连接整个 workspace。"),
        bullet("Inbox 和敏感信息默认不进入 AI Index。"),
    ]
    hub = client.create_child_page(parent_page_id, args.title, hub_children, emoji="🧠")
    hub_id = hub["id"]

    schemas = database_schemas()
    icons = database_icons()
    database_ids: dict[str, str] = {}
    database_urls: dict[str, str] = {}
    for database_name, schema in schemas.items():
        created = client.create_database(
            hub_id,
            database_name,
            schema,
            emoji=icons.get(database_name),
        )
        database_ids[database_name] = created["id"]
        database_urls[database_name] = created.get("url", "")

    if not args.no_samples:
        create_sample_rows(client, database_ids, schemas, Path(args.template_dir))

    result = {
        "hub": {"id": hub_id, "url": hub.get("url", "")},
        "databases": {
            name: {"id": database_ids[name], "url": database_urls.get(name, "")}
            for name in database_ids
        },
        "notion_version": args.notion_version,
        "dry_run": args.dry_run,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.dry_run:
        output_path = Path(args.output)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nWrote setup result to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
