from __future__ import annotations

import json
import logging
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dashboard.data import build_dashboard_payload, latest_backtest_chart_path


STATIC_DIR = Path(__file__).resolve().parent / "static"
DASHBOARD_BASE_PATH = os.getenv("DASHBOARD_BASE_PATH", "").strip().rstrip("/")
if DASHBOARD_BASE_PATH and not DASHBOARD_BASE_PATH.startswith("/"):
    DASHBOARD_BASE_PATH = f"/{DASHBOARD_BASE_PATH}"
logger = logging.getLogger(__name__)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AStockDashboard/1.8"

    def do_GET(self) -> None:
        path = self._normalized_path()
        if path is None:
            return
        if path in {"", "/"}:
            self._send_file(STATIC_DIR / "index.html")
            return
        if path == "/api/ping":
            self._send_json({"ok": True, "service": "dashboard", "version": "v1.8"})
            return
        if path == "/api/dashboard":
            self._send_json(build_dashboard_payload())
            return
        if path == "/api/backtest-chart.svg":
            chart = latest_backtest_chart_path()
            if chart is None:
                self._send_text("No backtest chart available.", HTTPStatus.NOT_FOUND)
                return
            self._send_file(chart, content_type="image/svg+xml")
            return
        if path.startswith("/static/"):
            relative = path.removeprefix("/static/")
            self._send_static(relative)
            return
        self._send_text("Not found.", HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        self._send_json(
            {
                "ok": False,
                "error": "Dashboard is read-only. No trading or mutation endpoints are exposed.",
            },
            HTTPStatus.METHOD_NOT_ALLOWED,
        )

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), format % args)

    def _normalized_path(self) -> str | None:
        path = urlparse(self.path).path
        if not DASHBOARD_BASE_PATH:
            return path
        if path == DASHBOARD_BASE_PATH:
            self._redirect(f"{DASHBOARD_BASE_PATH}/")
            return None
        if path.startswith(f"{DASHBOARD_BASE_PATH}/"):
            stripped = path[len(DASHBOARD_BASE_PATH) :]
            return stripped or "/"
        self._send_text("Not found.", HTTPStatus.NOT_FOUND)
        return None

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.MOVED_PERMANENTLY)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _send_static(self, relative: str) -> None:
        try:
            path = (STATIC_DIR / relative).resolve()
        except OSError:
            self._send_text("Invalid path.", HTTPStatus.BAD_REQUEST)
            return
        if not str(path).startswith(str(STATIC_DIR.resolve())) or not path.is_file():
            self._send_text("Not found.", HTTPStatus.NOT_FOUND)
            return
        self._send_file(path)

    def _send_file(self, path: Path, content_type: str | None = None) -> None:
        if not path.exists() or not path.is_file():
            self._send_text("Not found.", HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        guessed_type = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", guessed_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_text(self, text: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    logger.info("dashboard server started on http://%s:%s", host, port)
    server.serve_forever()


if __name__ == "__main__":
    run()
