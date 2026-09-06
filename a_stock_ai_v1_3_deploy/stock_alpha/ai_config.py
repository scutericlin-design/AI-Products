"""Strategy-local AI configuration and a non-trading connectivity check."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import urlsplit

import requests


DEFAULT_CONFIG = Path(__file__).resolve().parent / "local_data" / "ai_config.json"


@dataclass(frozen=True)
class AISettings:
    model: str = "deepseek-v4-flash"
    base_url: str = "https://tbtk.asia/v1"
    api_key: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) for value in (self.model, self.base_url, self.api_key)):
            raise ValueError("AI configuration values must be strings")
        if not self.model.strip() or any(char.isspace() for char in self.model):
            raise ValueError("AI model must be a nonempty identifier")
        url = urlsplit(self.base_url)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or any(char.isspace() for char in self.base_url)):
            raise ValueError("AI base URL must be an HTTPS URL without credentials or query parameters")
        if self.api_key and any(char.isspace() for char in self.api_key):
            raise ValueError("AI key must not contain whitespace")

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def public_config(self) -> dict:
        return {"strategy": "stock_alpha", "model": self.model, "base_url": self.base_url,
                "api_key_configured": bool(self.api_key), "no_orders": True}


def load_ai_settings(path: Path | None = None, *, environ: Mapping[str, str] | None = None) -> AISettings:
    environment = os.environ if environ is None else environ
    if path is None:
        path = Path(environment.get("STOCK_ALPHA_AI_CONFIG") or DEFAULT_CONFIG)
    configured = {}
    if path.exists():
        try:
            configured = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("Cannot read stock_alpha AI configuration; contents redacted") from None
        if not isinstance(configured, dict) or set(configured) - {"model", "base_url", "api_key"}:
            raise ValueError("Unexpected stock_alpha AI configuration structure")
    defaults = AISettings()
    values = {}
    for name in ("model", "base_url", "api_key"):
        values[name] = environment.get(f"STOCK_ALPHA_AI_{name.upper()}",
                                       configured.get(name, getattr(defaults, name)))
    return AISettings(**values)


def check_connection(settings: AISettings) -> dict:
    result = {**settings.public_config(), "ok": False, "check": "chat_completion"}
    if not settings.api_key:
        return {**result, "error": "missing_api_key"}
    try:
        # A fixed probe only: no account details, financial records or orders are sent.
        response = requests.post(
            settings.endpoint,
            headers={"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"},
            json={"model": settings.model, "messages": [{"role": "user", "content": "Reply with OK only."}],
                  "temperature": 0, "max_tokens": 128},
            timeout=(10, 45), allow_redirects=False,
        )
        result["http_status"] = response.status_code
        if response.status_code != 200:
            return {**result, "error": "provider_http_error"}
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            return {**result, "error": "empty_completion"}
        reported_model = payload.get("model")
        if (not isinstance(reported_model, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", reported_model)
                or settings.api_key in reported_model):
            reported_model = None
        return {**result, "ok": True, "completion_received": True,
                "response_model": reported_model,
                "response_model_matches": payload.get("model") == settings.model}
    except requests.RequestException:
        return {**result, "error": "network_error"}
    except (ValueError, TypeError, KeyError, IndexError):
        return {**result, "error": "invalid_completion_payload"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--check", action="store_true", help="Send one tiny non-trading API request.")
    args = parser.parse_args()
    try:
        settings = load_ai_settings(args.config)
    except (ValueError, OSError):
        print(json.dumps({"ok": False, "error": "invalid_configuration", "no_orders": True}))
        raise SystemExit(2) from None
    result = check_connection(settings) if args.check else settings.public_config()
    print(json.dumps(result))
    if args.check and not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
