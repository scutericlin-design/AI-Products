#!/usr/bin/env python3
"""Local web app server for the A-share investment workstation.

It serves the OpenDesign prototype and exposes local-only API endpoints used by
the page buttons. This keeps user operations inside the browser while the
server performs trusted local Python tasks.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RECOMMENDED_POOL_PATH = PROJECT_ROOT / "data" / "processed" / "recommended_pool.csv"


def count_recommended_pool_rows() -> int:
    if not RECOMMENDED_POOL_PATH.exists():
        return 0
    with RECOMMENDED_POOL_PATH.open("r", encoding="utf-8-sig") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


class LocalAppHandler(SimpleHTTPRequestHandler):
    server_version = "AShareAlphaLocal/0.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def write_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path == "/api/rebuild-system-pool":
            self.rebuild_system_pool()
            return
        self.write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown API endpoint"})

    def rebuild_system_pool(self) -> None:
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "build_a_share_spot_pool.py"),
            "--limit",
            "30",
            "--min-amount",
            "300000000",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.write_json(
                HTTPStatus.REQUEST_TIMEOUT,
                {
                    "ok": False,
                    "error": "全 A 股扫描超时，请稍后重试。",
                },
            )
            return

        if completed.returncode != 0:
            self.write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "error": "全 A 股扫描失败。",
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                },
            )
            return

        self.write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "message": "系统股票池已更新。",
                "rows": count_recommended_pool_rows(),
                "stdout": completed.stdout[-4000:],
            },
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local A-share workstation web server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8289)
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), LocalAppHandler)
    print(f"Local app server: http://{args.host}:{args.port}/opendesign/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping local app server.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
