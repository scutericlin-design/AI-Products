#!/usr/bin/env python3
"""Export a Notion Research Pack into a study-workspace-ready source pack.

The output is a single Markdown file and a plain-text twin. Upload the Markdown
file if your study workspace accepts it, or use the plain-text file as copied
text.

Required:
  NOTION_API_KEY or --token/--token-file

Examples:
  python3 scripts/export_notion_to_notebooklm_pack.py --list
  python3 scripts/export_notion_to_notebooklm_pack.py --research-pack-title "2026-06 Knowledge Stack - Notion Onyx NotebookLM"
  python3 scripts/export_notion_to_notebooklm_pack.py --target-label ima --research-pack-title "2026-06 中学生英语提升与高考优胜策略"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import textwrap
import time
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
        with urllib.request.urlopen(req, timeout=45) as response:
            time.sleep(0.35)
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notion API error {exc.code} for {method} {path}: {body}") from exc


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


def plain_text(items: list[dict[str, Any]]) -> str:
    return "".join(item.get("plain_text", "") for item in items or [])


def title_from_properties(properties: dict[str, Any]) -> str:
    for prop in properties.values():
        if prop.get("type") == "title":
            return plain_text(prop.get("title", [])) or "Untitled"
    return "Untitled"


def prop_value(prop: dict[str, Any]) -> str:
    prop_type = prop.get("type")
    if prop_type == "title":
        return plain_text(prop.get("title", []))
    if prop_type == "rich_text":
        return plain_text(prop.get("rich_text", []))
    if prop_type == "select":
        selected = prop.get("select")
        return selected.get("name", "") if selected else ""
    if prop_type == "multi_select":
        return ", ".join(item.get("name", "") for item in prop.get("multi_select", []))
    if prop_type == "url":
        return prop.get("url") or ""
    if prop_type == "checkbox":
        return "Yes" if prop.get("checkbox") else "No"
    if prop_type == "number":
        number = prop.get("number")
        return "" if number is None else str(number)
    if prop_type == "date":
        date = prop.get("date")
        if not date:
            return ""
        start = date.get("start", "")
        end = date.get("end")
        return f"{start} -> {end}" if end else start
    if prop_type == "created_time":
        return prop.get("created_time", "")
    if prop_type == "last_edited_time":
        return prop.get("last_edited_time", "")
    return ""


def query_database(
    token: str,
    database_id_value: str,
    notion_version: str,
    payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    payload = dict(payload or {})
    results: list[dict[str, Any]] = []
    cursor = None
    while True:
        if cursor:
            payload["start_cursor"] = cursor
        payload.setdefault("page_size", 50)
        response = request(token, "POST", f"/databases/{database_id_value}/query", payload, notion_version)
        results.extend(response.get("results", []))
        if not response.get("has_more"):
            return results
        cursor = response.get("next_cursor")


def find_research_pack(
    token: str,
    db_id: str,
    title: str | None,
    page_id: str | None,
    notion_version: str,
) -> dict[str, Any]:
    if page_id:
        return request(token, "GET", f"/pages/{page_id}", None, notion_version)
    if not title:
        raise RuntimeError("Provide --research-pack-title or --research-pack-id.")
    payload = {
        "filter": {"property": "Name", "title": {"equals": title}},
        "page_size": 1,
    }
    rows = query_database(token, db_id, notion_version, payload)
    if not rows:
        raise RuntimeError(f"No Research Pack found with title: {title}")
    return rows[0]


def list_research_packs(token: str, db_id: str, notion_version: str) -> None:
    payload = {"sorts": [{"timestamp": "last_edited_time", "direction": "descending"}], "page_size": 25}
    rows = query_database(token, db_id, notion_version, payload)
    for row in rows:
        title = title_from_properties(row.get("properties", {}))
        props = row.get("properties", {})
        status = prop_value(props.get("Status", {}))
        notebook = prop_value(props.get("NotebookLM URL", {}))
        edited = row.get("last_edited_time", "")
        print(f"- {title} | status={status or '-'} | notebook={notebook or '-'} | edited={edited}")


def get_block_children(
    token: str,
    block_id: str,
    notion_version: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    cursor = None
    while True:
        suffix = f"?page_size=100"
        if cursor:
            suffix += f"&start_cursor={cursor}"
        response = request(token, "GET", f"/blocks/{block_id}/children{suffix}", None, notion_version)
        results.extend(response.get("results", []))
        if not response.get("has_more"):
            return results
        cursor = response.get("next_cursor")


def block_text(block: dict[str, Any]) -> str:
    block_type = block.get("type", "")
    data = block.get(block_type, {})
    return plain_text(data.get("rich_text", []))


def target_text(text: str, target_label: str) -> str:
    if target_label.strip().lower() == "notebooklm":
        return text
    return text.replace("NotebookLM", target_label.strip() or "Study Workspace")


def render_blocks(
    token: str,
    blocks: list[dict[str, Any]],
    notion_version: str,
    target_label: str,
    depth: int = 0,
    max_depth: int = 2,
) -> str:
    lines: list[str] = []
    for block in blocks:
        block_type = block.get("type", "")
        text = target_text(block_text(block).strip(), target_label)
        indent = "  " * depth

        if block_type == "paragraph":
            if text:
                lines.append(f"{indent}{text}")
                lines.append("")
        elif block_type == "heading_1":
            lines.append(f"# {text}")
            lines.append("")
        elif block_type == "heading_2":
            lines.append(f"## {text}")
            lines.append("")
        elif block_type == "heading_3":
            lines.append(f"### {text}")
            lines.append("")
        elif block_type == "bulleted_list_item":
            lines.append(f"{indent}- {text}")
        elif block_type == "numbered_list_item":
            lines.append(f"{indent}1. {text}")
        elif block_type == "to_do":
            checked = block.get("to_do", {}).get("checked")
            lines.append(f"{indent}- [{'x' if checked else ' '}] {text}")
        elif block_type == "quote":
            lines.append(f"{indent}> {text}")
            lines.append("")
        elif block_type == "callout":
            lines.append(f"{indent}> {text}")
            lines.append("")
        elif block_type == "code":
            language = block.get("code", {}).get("language", "")
            lines.append(f"```{language}")
            lines.append(text)
            lines.append("```")
            lines.append("")
        elif block_type == "divider":
            lines.append("---")
            lines.append("")
        elif block_type == "child_page":
            title = block.get("child_page", {}).get("title", "Child Page")
            lines.append(f"{indent}- Child page: {title}")
        elif block_type == "bookmark":
            url = block.get("bookmark", {}).get("url", "")
            caption = plain_text(block.get("bookmark", {}).get("caption", []))
            lines.append(f"{indent}- Bookmark: {caption or url} {url}".strip())
        elif block_type == "link_preview":
            url = block.get("link_preview", {}).get("url", "")
            lines.append(f"{indent}- Link preview: {url}")
        elif block_type == "unsupported":
            lines.append(f"{indent}- [Unsupported Notion block omitted]")
        else:
            if text:
                lines.append(f"{indent}- {text}")

        if block.get("has_children") and depth < max_depth:
            child_blocks = get_block_children(token, block["id"], notion_version)
            rendered = render_blocks(token, child_blocks, notion_version, target_label, depth + 1, max_depth)
            if rendered:
                lines.append(rendered.rstrip())
                lines.append("")

    return "\n".join(lines).strip() + "\n"


def render_page(
    token: str,
    page: dict[str, Any],
    notion_version: str,
    max_depth: int,
    target_label: str,
) -> str:
    title = title_from_properties(page.get("properties", {}))
    url = page.get("url", "")
    properties = page.get("properties", {})
    body = render_blocks(
        token,
        get_block_children(token, page["id"], notion_version),
        notion_version,
        target_label,
        0,
        max_depth,
    )

    prop_lines = []
    is_notebooklm_target = target_label.strip().lower() == "notebooklm"
    for name in sorted(properties.keys()):
        if not is_notebooklm_target and name.startswith("NotebookLM"):
            continue
        value = prop_value(properties[name])
        if value:
            prop_lines.append(f"- {target_text(name, target_label)}: {target_text(value, target_label)}")

    parts = [f"## {target_text(title, target_label)}", ""]
    if url:
        parts.extend([f"Source URL: {url}", ""])
    if prop_lines:
        parts.extend(["### Properties", "", "\n".join(prop_lines), ""])
    if body.strip():
        parts.extend(["### Body", "", body.strip(), ""])
    return "\n".join(parts).strip() + "\n"


def relation_filter(property_name: str, title: str) -> dict[str, Any]:
    return {"filter": {"property": property_name, "rich_text": {"contains": title}}, "page_size": 50}


def safe_related_query(
    token: str,
    db_id: str,
    notion_version: str,
    property_name: str,
    title: str,
) -> list[dict[str, Any]]:
    try:
        return query_database(token, db_id, notion_version, relation_filter(property_name, title))
    except RuntimeError as exc:
        print(f"Warning: could not query related rows on {property_name}: {exc}", file=sys.stderr)
        return []


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:90] or "study-source-pack"


def markdown_to_plain_text(markdown: str) -> str:
    text = re.sub(r"```.*?```", lambda m: m.group(0).replace("```", ""), markdown, flags=re.S)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text


def update_pack_export_status(
    token: str,
    page_id: str,
    source_path: Path,
    export_hash: str,
    notion_version: str,
) -> None:
    now = datetime.now(timezone.utc).date().isoformat()
    properties = {
        "NotebookLM Sync Status": {"select": {"name": "Exported"}},
        "NotebookLM Source Pack Path": {"rich_text": [{"type": "text", "text": {"content": str(source_path)}}]},
        "NotebookLM Last Exported": {"date": {"start": now}},
        "NotebookLM Export Hash": {"rich_text": [{"type": "text", "text": {"content": export_hash}}]},
    }
    request(token, "PATCH", f"/pages/{page_id}", {"properties": properties}, notion_version)


def build_source_pack(
    token: str,
    setup: dict[str, Any],
    research_pack: dict[str, Any],
    notion_version: str,
    max_depth: int,
    target_label: str,
) -> str:
    title = title_from_properties(research_pack.get("properties", {}))
    exported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    setup_url = setup.get("hub", {}).get("url", "")
    target_name = target_label.strip() or "Study Workspace"

    sections = [
        f"# {target_name} Source Pack: {title}",
        "",
        f"Generated at: {exported_at}",
        f"AI Knowledge Hub: {setup_url}",
        "",
        f"Use this pack as one {target_name} source. After {target_name} produces useful outputs, write the conclusions back to the Notion Research Pack.",
        "",
        "# Research Pack",
        "",
        render_page(token, research_pack, notion_version, max_depth, target_name).strip(),
    ]

    related_specs = [
        ("Related Source Library Items", "Source Library", "Added For"),
        ("Related Evergreen Notes", "Evergreen Notes", "Related Research Pack"),
        ("Related Decisions", "Decisions", "Related Research Pack"),
        ("Related Projects", "Projects", "Related Research Packs"),
        ("Related AI Outputs", "AI Outputs", "Related Research Pack"),
    ]
    for heading, db_name, relation_property in related_specs:
        rows = safe_related_query(
            token,
            database_id(setup, db_name),
            notion_version,
            relation_property,
            title,
        )
        sections.extend(["", f"# {heading}", ""])
        if not rows:
            sections.append("No related rows found.")
            continue
        for row in rows:
            sections.append(render_page(token, row, notion_version, max_depth, target_name).strip())
            sections.append("")

    sections.extend(
        [
            "",
            f"# Suggested {target_name} Prompts",
            "",
            "1. 请先梳理这个 source pack 中所有资料的 Source Map：每个来源的核心观点、适合回答的问题、重叠和冲突。",
            "2. 围绕 Research Question 生成 Evidence Table，列出 Claim / Evidence / Source / Confidence / Caveat。",
            "3. 写一份 Decision Memo，明确区分事实、推论和建议。",
            "4. 提炼 3-7 条适合回写 Notion 的 Evergreen Notes。",
            "5. 生成适合 Audio Overview 的引导说明，要求密度高、少寒暄、重点讲判断和冲突。",
        ]
    )
    return "\n".join(sections).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", default=None)
    parser.add_argument("--token-file", default=os.getenv("NOTION_API_KEY_FILE"))
    parser.add_argument(
        "--setup-json",
        default=str(Path(__file__).resolve().parents[1] / "notion-setup-result.json"),
    )
    parser.add_argument("--notion-version", default=os.getenv("NOTION_VERSION", DEFAULT_NOTION_VERSION))
    parser.add_argument("--research-pack-title")
    parser.add_argument("--research-pack-id")
    parser.add_argument("--list", action="store_true", help="List recent Research Packs and exit.")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parents[1] / "notebooklm-source-packs"),
    )
    parser.add_argument("--target-label", default="NotebookLM")
    parser.add_argument("--recommended-source-type", default="file upload / copied text")
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--update-notion", action="store_true")
    args = parser.parse_args()

    token = load_token(args)
    setup = json.loads(Path(args.setup_json).read_text(encoding="utf-8"))
    research_pack_db_id = database_id(setup, "Research Packs")

    if args.list:
        list_research_packs(token, research_pack_db_id, args.notion_version)
        return 0

    research_pack = find_research_pack(
        token,
        research_pack_db_id,
        args.research_pack_title,
        args.research_pack_id,
        args.notion_version,
    )
    title = title_from_properties(research_pack.get("properties", {}))
    markdown = build_source_pack(
        token,
        setup,
        research_pack,
        args.notion_version,
        args.max_depth,
        args.target_label,
    )
    export_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / f"{slugify(title)}-{export_hash}"
    markdown_path = base.with_suffix(".md")
    text_path = base.with_suffix(".txt")
    manifest_path = base.with_suffix(".json")

    markdown_path.write_text(markdown, encoding="utf-8")
    text_path.write_text(markdown_to_plain_text(markdown), encoding="utf-8")
    manifest = {
        "title": title,
        "research_pack_id": research_pack["id"],
        "research_pack_url": research_pack.get("url", ""),
        "markdown_path": str(markdown_path),
        "text_path": str(text_path),
        "export_hash": export_hash,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_label": args.target_label,
        "recommended_source_type": args.recommended_source_type,
        "recommended_notebooklm_source_type": "Copied text",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.update_notion:
        update_pack_export_status(token, research_pack["id"], text_path, export_hash, args.notion_version)

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
