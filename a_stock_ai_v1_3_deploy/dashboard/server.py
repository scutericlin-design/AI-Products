from __future__ import annotations

import json
import logging
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from dashboard.auth import (
    AuthIdentity,
    auth_enabled,
    approve_access_request,
    clear_cookie_header,
    configured_username,
    create_access_request,
    current_identity,
    is_admin,
    list_access_requests,
    list_dashboard_users,
    make_session_cookie,
    password_hash_configured,
    reject_access_request,
    session_cookie_header,
    set_user_status,
    verify_credentials,
    verify_session_cookie,
)
from dashboard.data import build_dashboard_payload, build_etf_dashboard_payload, latest_backtest_chart_path
from hot_leader_strategy.dashboard import build_hot_leader_dashboard_payload
from qgarp_strategy.dashboard import build_qgarp_dashboard_payload
from sentiment_quant_dashboard import build_sentiment_quant_dashboard_payload
from dashboard.stock_alpha_data import build_stock_alpha_payload


STATIC_DIR = Path(__file__).resolve().parent / "static"
DASHBOARD_BASE_PATH = os.getenv("DASHBOARD_BASE_PATH", "").strip().rstrip("/")
if DASHBOARD_BASE_PATH and not DASHBOARD_BASE_PATH.startswith("/"):
    DASHBOARD_BASE_PATH = f"/{DASHBOARD_BASE_PATH}"
logger = logging.getLogger(__name__)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AStockDashboard/1.9"

    def do_GET(self) -> None:
        path = self._normalized_path()
        if path is None:
            return
        if path == "/login":
            if self._is_authenticated():
                self._redirect(self._base_url("/"))
                return
            self._send_login_page()
            return
        if path == "/apply":
            self._send_apply_page()
            return
        if path == "/admin":
            if not self._require_admin(path):
                return
            self._send_admin_page()
            return
        if path == "/logout":
            self._redirect(self._base_url("/login"), headers=[("Set-Cookie", clear_cookie_header(self._cookie_path()))])
            return
        if path in {"", "/"}:
            if not self._require_auth(path):
                return
            self._send_file(STATIC_DIR / "index.html")
            return
        if path == "/etf":
            if not self._require_auth(path):
                return
            self._send_file(STATIC_DIR / "etf.html")
            return
        if path == "/qgarp":
            if not self._require_auth(path):
                return
            self._send_file(STATIC_DIR / "qgarp.html")
            return
        if path == "/hot-leader":
            if not self._require_auth(path):
                return
            self._send_file(STATIC_DIR / "hot_leader.html")
            return
        if path == "/sentiment-quant":
            if not self._require_auth(path):
                return
            self._send_file(STATIC_DIR / "sentiment_quant.html")
            return
        if path == "/stock-alpha":
            if not self._require_auth(path):
                return
            self._send_file(STATIC_DIR / "stock_alpha.html")
            return
        if path == "/api/stock-alpha-dashboard":
            if not self._require_auth(path):
                return
            self._send_json(build_stock_alpha_payload())
            return
        if path == "/api/ping":
            self._send_json(
                {
                    "ok": True,
                    "service": "dashboard",
                    "version": "v1.9",
                    "auth_enabled": auth_enabled(),
                }
            )
            return
        if path == "/api/dashboard":
            if not self._require_auth(path):
                return
            self._send_json(build_dashboard_payload())
            return
        if path == "/api/etf-dashboard":
            if not self._require_auth(path):
                return
            self._send_json(build_etf_dashboard_payload())
            return
        if path == "/api/qgarp-dashboard":
            if not self._require_auth(path):
                return
            self._send_json(build_qgarp_dashboard_payload())
            return
        if path == "/api/hot-leader-dashboard":
            if not self._require_auth(path):
                return
            self._send_json(build_hot_leader_dashboard_payload())
            return
        if path == "/api/sentiment-quant-dashboard":
            if not self._require_auth(path):
                return
            self._send_json(build_sentiment_quant_dashboard_payload())
            return
        if path == "/api/backtest-chart.svg":
            if not self._require_auth(path):
                return
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
        path = self._normalized_path()
        if path is None:
            return
        if path == "/login":
            self._handle_login()
            return
        if path == "/apply":
            self._handle_apply()
            return
        if path.startswith("/admin/"):
            self._handle_admin_action(path)
            return
        if path == "/logout":
            self._redirect(self._base_url("/login"), headers=[("Set-Cookie", clear_cookie_header(self._cookie_path()))])
            return
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
            self._redirect(f"{DASHBOARD_BASE_PATH}/", status=HTTPStatus.MOVED_PERMANENTLY)
            return None
        if path.startswith(f"{DASHBOARD_BASE_PATH}/"):
            stripped = path[len(DASHBOARD_BASE_PATH) :]
            return stripped or "/"
        self._send_text("Not found.", HTTPStatus.NOT_FOUND)
        return None

    def _redirect(
        self,
        location: str,
        headers: list[tuple[str, str]] | None = None,
        status: HTTPStatus = HTTPStatus.SEE_OTHER,
    ) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        for key, value in headers or []:
            self.send_header(key, value)
        self.end_headers()

    def _require_auth(self, path: str) -> bool:
        if self._is_authenticated():
            return True
        if path.startswith("/api/"):
            self._send_json({"ok": False, "error": "authentication_required"}, HTTPStatus.UNAUTHORIZED)
        else:
            next_path = quote(self._base_url(path or "/"), safe="/?=&")
            self._redirect(self._base_url(f"/login?next={next_path}"))
        return False

    def _require_admin(self, path: str) -> bool:
        identity = self._identity()
        if is_admin(identity):
            return True
        if identity is None:
            next_path = quote(self._base_url(path or "/admin"), safe="/?=&")
            self._redirect(self._base_url(f"/login?next={next_path}"))
        else:
            self._send_text("Forbidden.", HTTPStatus.FORBIDDEN)
        return False

    def _is_authenticated(self) -> bool:
        return verify_session_cookie(self.headers.get("Cookie"))

    def _identity(self) -> AuthIdentity | None:
        return current_identity(self.headers.get("Cookie"))

    def _handle_login(self) -> None:
        if not auth_enabled():
            self._redirect(self._base_url("/"))
            return
        length = min(int(self.headers.get("Content-Length") or 0), 16_384)
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        form = parse_qs(body, keep_blank_values=True)
        username = (form.get("username") or [""])[0]
        password = (form.get("password") or [""])[0]
        next_url = (form.get("next") or [self._base_url("/")])[0] or self._base_url("/")
        identity = verify_credentials(username, password)
        if identity:
            cookie = session_cookie_header(make_session_cookie(identity), self._cookie_path())
            self._redirect(self._safe_next_url(next_url), headers=[("Set-Cookie", cookie)])
            return
        logger.warning("dashboard login failed for username=%s", username)
        self._send_login_page(error="用户名或密码错误", next_url=next_url, status=HTTPStatus.UNAUTHORIZED)

    def _handle_apply(self) -> None:
        form = self._read_form()
        result = create_access_request(
            email=(form.get("email") or [""])[0],
            name=(form.get("name") or [""])[0],
            password=(form.get("password") or [""])[0],
            note=(form.get("note") or [""])[0],
        )
        if result.get("ok"):
            self._send_apply_page(
                message="申请已提交。管理员批准后，该邮箱即可使用申请时填写的密码登录。",
                status=HTTPStatus.CREATED,
            )
        else:
            self._send_apply_page(error=str(result.get("error") or "申请失败"), status=HTTPStatus.BAD_REQUEST)

    def _handle_admin_action(self, path: str) -> None:
        identity = self._identity()
        if not is_admin(identity):
            self._send_text("Forbidden.", HTTPStatus.FORBIDDEN)
            return
        form = self._read_form()
        admin_email = identity.email if identity else configured_username()
        if path == "/admin/requests/approve":
            approve_access_request(int((form.get("request_id") or ["0"])[0]), admin_email)
        elif path == "/admin/requests/reject":
            reject_access_request(int((form.get("request_id") or ["0"])[0]), admin_email)
        elif path == "/admin/users/disable":
            set_user_status((form.get("email") or [""])[0], "disabled", admin_email)
        elif path == "/admin/users/enable":
            set_user_status((form.get("email") or [""])[0], "active", admin_email)
        else:
            self._send_text("Not found.", HTTPStatus.NOT_FOUND)
            return
        self._redirect(self._base_url("/admin"))

    def _send_login_page(
        self,
        error: str | None = None,
        next_url: str | None = None,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        query = parse_qs(urlparse(self.path).query)
        target = next_url or (query.get("next") or [self._base_url("/")])[0]
        setup_message = ""
        disabled_message = ""
        if not auth_enabled():
            disabled_message = "认证当前未启用。"
        elif not password_hash_configured():
            setup_message = "认证已启用，但服务器未配置 DASHBOARD_AUTH_PASSWORD_HASH。"
        body = _login_html(
            base_path=DASHBOARD_BASE_PATH or "",
            username=configured_username(),
            next_url=self._safe_next_url(target),
            error=error,
            setup_message=setup_message,
            disabled_message=disabled_message,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_apply_page(
        self,
        message: str | None = None,
        error: str | None = None,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = _apply_html(
            base_path=DASHBOARD_BASE_PATH or "",
            message=message,
            error=error,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_admin_page(self) -> None:
        body = _admin_html(
            base_path=DASHBOARD_BASE_PATH or "",
            identity=self._identity(),
            requests=list_access_requests(),
            users=list_dashboard_users(),
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_form(self) -> dict[str, list[str]]:
        length = min(int(self.headers.get("Content-Length") or 0), 32_768)
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        return parse_qs(body, keep_blank_values=True)

    def _base_url(self, path: str) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        return f"{DASHBOARD_BASE_PATH}{normalized}" if DASHBOARD_BASE_PATH else normalized

    def _cookie_path(self) -> str:
        return f"{DASHBOARD_BASE_PATH}/" if DASHBOARD_BASE_PATH else "/"

    def _safe_next_url(self, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme or parsed.netloc:
            return self._base_url("/")
        if DASHBOARD_BASE_PATH:
            return value if value.startswith(f"{DASHBOARD_BASE_PATH}/") else self._base_url("/")
        return value if value.startswith("/") else "/"

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


def _login_html(
    base_path: str,
    username: str,
    next_url: str,
    error: str | None = None,
    setup_message: str = "",
    disabled_message: str = "",
) -> str:
    error_html = f'<p class="login-error">{error}</p>' if error else ""
    setup_html = f'<p class="login-error">{setup_message}</p>' if setup_message else ""
    disabled_html = f'<p class="login-note">{disabled_message}</p>' if disabled_message else ""
    action = f"{base_path}/login" if base_path else "/login"
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>A股 Dashboard 登录</title>
    <link rel="stylesheet" href="{base_path}/static/dashboard.css" />
  </head>
  <body class="login-body">
    <main class="login-shell">
      <section class="login-panel">
        <p class="eyebrow">Protected Dashboard</p>
        <h1>A股实时感知 Dashboard</h1>
        <p class="login-copy">请输入账号/邮箱和密码。该页面只读，不提供实盘下单入口。</p>
        {error_html}
        {setup_html}
        {disabled_html}
        <form method="post" action="{action}" class="login-form">
          <input type="hidden" name="next" value="{_escape(next_url)}" />
          <label>
            <span>用户名或邮箱</span>
            <input name="username" autocomplete="username" value="{_escape(username)}" required />
          </label>
          <label>
            <span>密码</span>
            <input name="password" type="password" autocomplete="current-password" required autofocus />
          </label>
          <button type="submit">登录</button>
        </form>
        <p class="login-note"><a href="{base_path}/apply">申请浏览权限</a></p>
      </section>
    </main>
  </body>
</html>
"""


def _apply_html(base_path: str, message: str | None = None, error: str | None = None) -> str:
    message_html = f'<p class="login-note">{_escape(message)}</p>' if message else ""
    error_html = f'<p class="login-error">{_escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>申请 Dashboard 权限</title>
    <link rel="stylesheet" href="{base_path}/static/dashboard.css" />
  </head>
  <body class="login-body">
    <main class="login-shell">
      <section class="login-panel">
        <p class="eyebrow">Access Request</p>
        <h1>申请浏览权限</h1>
        <p class="login-copy">提交邮箱和密码。管理员批准后，该邮箱才能登录浏览 Dashboard。</p>
        {message_html}
        {error_html}
        <form method="post" action="{base_path}/apply" class="login-form">
          <label>
            <span>邮箱</span>
            <input name="email" type="email" autocomplete="email" required autofocus />
          </label>
          <label>
            <span>姓名</span>
            <input name="name" autocomplete="name" />
          </label>
          <label>
            <span>密码</span>
            <input name="password" type="password" autocomplete="new-password" minlength="8" required />
          </label>
          <label>
            <span>备注</span>
            <input name="note" placeholder="申请原因，可选" />
          </label>
          <button type="submit">提交申请</button>
        </form>
        <p class="login-note"><a href="{base_path}/login">返回登录</a></p>
      </section>
    </main>
  </body>
</html>
"""


def _admin_html(
    base_path: str,
    identity: AuthIdentity | None,
    requests: list[dict[str, Any]],
    users: list[dict[str, Any]],
) -> str:
    pending_rows = "\n".join(_request_row(base_path, item) for item in requests) or (
        '<tr><td colspan="7"><div class="empty">暂无申请记录。</div></td></tr>'
    )
    user_rows = "\n".join(_user_row(base_path, item) for item in users) or (
        '<tr><td colspan="7"><div class="empty">暂无浏览用户。</div></td></tr>'
    )
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Dashboard 后台管理</title>
    <link rel="stylesheet" href="{base_path}/static/dashboard.css" />
  </head>
  <body>
    <div class="shell">
      <header class="topbar">
        <div>
          <p class="eyebrow">Admin Console</p>
          <h1>Dashboard 后台管理</h1>
        </div>
        <div class="topbar__actions">
          <a class="nav-button" href="{base_path}/">返回 Dashboard</a>
          <form method="post" action="{base_path}/logout" class="logout-form"><button type="submit">退出</button></form>
        </div>
      </header>
      <main class="grid">
        <section class="panel">
          <div class="panel__head">
            <div>
              <p class="eyebrow">Requests</p>
              <h2>用户申请</h2>
            </div>
            <span class="badge">管理员：{_escape(identity.email if identity else "")}</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>邮箱</th><th>姓名</th><th>备注</th><th>状态</th><th>申请时间</th><th>审核</th><th>操作</th></tr>
              </thead>
              <tbody>{pending_rows}</tbody>
            </table>
          </div>
        </section>
        <section class="panel">
          <div class="panel__head">
            <div>
              <p class="eyebrow">Users</p>
              <h2>已授权用户</h2>
            </div>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr><th>邮箱</th><th>姓名</th><th>角色</th><th>状态</th><th>批准时间</th><th>最后登录</th><th>操作</th></tr>
              </thead>
              <tbody>{user_rows}</tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  </body>
</html>
"""


def _request_row(base_path: str, item: dict[str, Any]) -> str:
    status = str(item.get("status") or "")
    actions = "-"
    if status == "pending":
        request_id = _escape(item.get("id"))
        actions = f"""
          <form method="post" action="{base_path}/admin/requests/approve" class="inline-form">
            <input type="hidden" name="request_id" value="{request_id}" />
            <button type="submit">批准</button>
          </form>
          <form method="post" action="{base_path}/admin/requests/reject" class="inline-form">
            <input type="hidden" name="request_id" value="{request_id}" />
            <button type="submit">拒绝</button>
          </form>
        """
    return f"""<tr>
      <td>{_escape(item.get("email"))}</td>
      <td>{_escape(item.get("name"))}</td>
      <td>{_escape(item.get("note"))}</td>
      <td>{_escape(status)}</td>
      <td>{_escape(item.get("requested_at"))}</td>
      <td>{_escape(item.get("reviewed_at") or "-")}</td>
      <td><div class="admin-actions">{actions}</div></td>
    </tr>"""


def _user_row(base_path: str, item: dict[str, Any]) -> str:
    status = str(item.get("status") or "")
    email = _escape(item.get("email"))
    if status == "active":
        action_path = "disable"
        label = "停用"
    else:
        action_path = "enable"
        label = "启用"
    actions = f"""
      <form method="post" action="{base_path}/admin/users/{action_path}" class="inline-form">
        <input type="hidden" name="email" value="{email}" />
        <button type="submit">{label}</button>
      </form>
    """
    return f"""<tr>
      <td>{email}</td>
      <td>{_escape(item.get("name"))}</td>
      <td>{_escape(item.get("role"))}</td>
      <td>{_escape(status)}</td>
      <td>{_escape(item.get("approved_at") or "-")}</td>
      <td>{_escape(item.get("last_login_at") or "-")}</td>
      <td><div class="admin-actions">{actions}</div></td>
    </tr>"""


def _escape(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#039;")
    )


if __name__ == "__main__":
    run()
