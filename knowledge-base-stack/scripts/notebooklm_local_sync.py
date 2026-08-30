#!/usr/bin/env python3
"""Download cloud-cached NotebookLM Source Packs to this computer.

This script is meant to run on the user's Mac, where VPN and Google login are
available. The cloud server only generates and caches Source Packs; this local
agent downloads them and prepares an upload queue for NotebookLM.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CLOUD_BASE_URL = "http://182.254.227.131"
DEFAULT_CACHE_DIR = Path.home() / "Documents" / "Hermes NotebookLM Source Packs"
DEFAULT_NOTEBOOKLM_URL = "https://notebooklm.google.com/"


def slugify(value: str, fallback: str = "notebooklm-source-pack") -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:90] or fallback


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def http_json(url: str, secret: str, timeout: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"X-Knowledge-Secret": secret})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def download_file(url: str, secret: str, destination: Path, timeout: int = 60) -> None:
    req = urllib.request.Request(url, headers={"X-Knowledge-Secret": secret})
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        destination.write_bytes(response.read())


def cloud_source_pack_url(base_url: str, job_key: str, kind: str) -> str:
    return f"{base_url.rstrip('/')}/api/knowledge/jobs/{urllib.parse.quote(job_key)}/source-pack/{kind}"


def job_target(job: dict[str, Any]) -> str:
    result = job.get("result") or {}
    study = result.get("study_workspace") or result.get("notebooklm") or {}
    return str(study.get("target") or "").lower()


def should_download(job: dict[str, Any], target: str) -> bool:
    if job.get("status") != "completed":
        return False
    if not job.get("source_pack"):
        return False
    if target == "all":
        return True
    current_target = job_target(job)
    return current_target in {"", target.lower()}


def download_job(job: dict[str, Any], base_url: str, secret: str, cache_dir: Path) -> dict[str, Any]:
    job_key = job["job_key"]
    title = job.get("title") or job.get("topic") or job_key
    folder = cache_dir / f"{slugify(title)}-{job_key}"
    manifest = job.get("source_pack") or {}
    downloads: dict[str, str] = {}
    for kind, suffix in (("md", ".md"), ("txt", ".txt")):
        source_key = "markdown_path" if kind == "md" else "text_path"
        if not manifest.get(source_key):
            continue
        filename = f"{slugify(title)}-{job_key}{suffix}"
        destination = folder / filename
        if not destination.exists():
            download_file(cloud_source_pack_url(base_url, job_key, kind), secret, destination)
        downloads[kind] = str(destination)

    local_manifest = {
        "job_key": job_key,
        "title": title,
        "topic": job.get("topic"),
        "notion_url": job.get("notion_url"),
        "notebooklm_url": job.get("notebooklm_url"),
        "target": job_target(job) or "notebooklm",
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "downloads": downloads,
        "cloud_source_pack": manifest,
    }
    write_json(folder / "manifest.json", local_manifest)
    return local_manifest


def copy_to_clipboard(path: Path) -> bool:
    if sys.platform != "darwin" or not path.exists():
        return False
    subprocess.run(["pbcopy"], input=path.read_bytes(), check=True)
    return True


def sync_once(args: argparse.Namespace) -> list[dict[str, Any]]:
    base_url = args.cloud_base_url.rstrip("/")
    secret = args.secret or os.getenv("KNOWLEDGE_COMMAND_SECRET") or os.getenv("HERMES_KNOWLEDGE_SECRET")
    if not secret:
        raise RuntimeError("Provide --secret or set KNOWLEDGE_COMMAND_SECRET / HERMES_KNOWLEDGE_SECRET.")

    cache_dir = Path(args.cache_dir).expanduser()
    state_path = cache_dir / ".state.json"
    queue_path = cache_dir / "upload_queue.json"
    state = read_json(state_path, {"downloaded_jobs": {}})
    downloaded_jobs = state.setdefault("downloaded_jobs", {})

    jobs_url = f"{base_url}/api/knowledge/jobs?limit={args.limit}"
    jobs = http_json(jobs_url, secret).get("jobs", [])
    downloaded: list[dict[str, Any]] = []
    queue = read_json(queue_path, {"items": []})
    queued_keys = {item.get("job_key") for item in queue.get("items", [])}

    for job in jobs:
        job_key = job.get("job_key")
        if not job_key or downloaded_jobs.get(job_key):
            continue
        if not should_download(job, args.target):
            continue
        local_manifest = download_job(job, base_url, secret, cache_dir)
        if args.sync_drive:
            drive_result = sync_drive(local_manifest, args)
            local_manifest["drive"] = drive_result
            write_json(Path(local_manifest["downloads"].get("txt", cache_dir)).parent / "manifest.json", local_manifest)
        downloaded_jobs[job_key] = {
            "downloaded_at": local_manifest["downloaded_at"],
            "title": local_manifest["title"],
            "target": local_manifest["target"],
            "downloads": local_manifest["downloads"],
            "drive": local_manifest.get("drive"),
        }
        if job_key not in queued_keys:
            queue_status = "drive_synced" if (local_manifest.get("drive") or {}).get("url") else "downloaded"
            queue.setdefault("items", []).append(
                {
                    "job_key": job_key,
                    "title": local_manifest["title"],
                    "status": queue_status,
                    "downloads": local_manifest["downloads"],
                    "drive": local_manifest.get("drive"),
                    "drive_file_id": (local_manifest.get("drive") or {}).get("file_id"),
                    "drive_name": (local_manifest.get("drive") or {}).get("name"),
                    "drive_url": (local_manifest.get("drive") or {}).get("url"),
                    "notion_url": local_manifest.get("notion_url"),
                    "notebooklm_url": local_manifest.get("notebooklm_url"),
                    "downloaded_at": local_manifest["downloaded_at"],
                }
            )
        downloaded.append(local_manifest)

    state["last_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_json(state_path, state)
    write_json(queue_path, queue)
    return downloaded


def sync_drive(local_manifest: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    folder_id = args.drive_folder_id or os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    token_file = args.google_oauth_token_file or os.getenv("GOOGLE_OAUTH_TOKEN_FILE")
    if not folder_id:
        return {"status": "skipped_no_folder_id"}
    if not token_file:
        return {"status": "skipped_no_oauth_token_file"}

    os.environ["GOOGLE_DRIVE_ENABLED"] = "true"
    os.environ["GOOGLE_DRIVE_FOLDER_ID"] = folder_id
    os.environ["GOOGLE_OAUTH_TOKEN_FILE"] = str(Path(token_file).expanduser())
    os.environ.setdefault("KNOWLEDGE_STUDY_WORKSPACE_LABEL", "NotebookLM")

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from app.services.knowledge_orchestrator import DriveSourcePackSync
    except Exception as exc:
        return {"status": "failed_import_drive_sync", "error": str(exc)[:1000]}

    manifest = dict(local_manifest.get("cloud_source_pack") or {})
    downloads = local_manifest.get("downloads") or {}
    if downloads.get("txt"):
        manifest["text_path"] = downloads["txt"]
    if downloads.get("md"):
        manifest["markdown_path"] = downloads["md"]
    manifest.setdefault("title", local_manifest.get("title"))
    return DriveSourcePackSync().sync(manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cloud-base-url", default=os.getenv("KNOWLEDGE_CLOUD_BASE_URL", DEFAULT_CLOUD_BASE_URL))
    parser.add_argument("--secret", default=None)
    parser.add_argument("--cache-dir", default=os.getenv("NOTEBOOKLM_LOCAL_CACHE_DIR", str(DEFAULT_CACHE_DIR)))
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--target", default="notebooklm", choices=["notebooklm", "ima", "all"])
    parser.add_argument("--watch", action="store_true", help="Keep polling for new completed jobs.")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--open-notebooklm", action="store_true")
    parser.add_argument("--copy-latest-text", action="store_true")
    parser.add_argument(
        "--sync-drive",
        action="store_true",
        default=os.getenv("NOTEBOOKLM_SYNC_DRIVE", "").lower() in {"1", "true", "yes"},
        help="After downloading, update the Google Drive Doc source with the local Source Pack.",
    )
    parser.add_argument("--drive-folder-id", default=os.getenv("GOOGLE_DRIVE_FOLDER_ID"))
    parser.add_argument("--google-oauth-token-file", default=os.getenv("GOOGLE_OAUTH_TOKEN_FILE"))
    args = parser.parse_args()

    while True:
        try:
            downloaded = sync_once(args)
            if downloaded:
                print(f"Downloaded {len(downloaded)} new Source Pack(s):")
                for item in downloaded:
                    print(f"- {item['title']} [{item['job_key']}]")
                    for kind, path in item.get("downloads", {}).items():
                        print(f"  {kind}: {path}")
                latest_txt = downloaded[0].get("downloads", {}).get("txt")
                if args.copy_latest_text and latest_txt and copy_to_clipboard(Path(latest_txt)):
                    print("Copied latest .txt Source Pack content to clipboard.")
                if args.open_notebooklm:
                    webbrowser.open(downloaded[0].get("notebooklm_url") or DEFAULT_NOTEBOOKLM_URL)
            else:
                print("No new NotebookLM Source Packs.")
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            print(f"Sync failed: {exc}", file=sys.stderr)
            if not args.watch:
                return 1

        if not args.watch:
            return 0
        time.sleep(max(args.interval_seconds, 30))


if __name__ == "__main__":
    raise SystemExit(main())
