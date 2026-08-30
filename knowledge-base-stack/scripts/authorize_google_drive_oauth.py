#!/usr/bin/env python3
"""Create a reusable Google Drive OAuth token for Hermes.

Run this on the Mac once. The generated token JSON can be copied to the cloud
server and used by Hermes to create/update Google Drive docs as the authorized
Google user.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-secret-file", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    from google_auth_oauthlib.flow import InstalledAppFlow

    client_secret_file = Path(args.client_secret_file).expanduser()
    token_file = Path(args.token_file).expanduser()
    if not client_secret_file.exists():
        raise FileNotFoundError(client_secret_file)

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), SCOPES)
    credentials = flow.run_local_server(
        host=args.host,
        port=args.port,
        authorization_prompt_message="Open this URL to authorize Hermes Google Drive access: {url}",
        success_message="Hermes Google Drive authorization is complete. You can close this browser tab.",
        open_browser=True,
        access_type="offline",
        prompt="consent",
    )
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")

    info = json.loads(credentials.to_json())
    print(
        json.dumps(
            {
                "token_file": str(token_file),
                "has_refresh_token": bool(info.get("refresh_token")),
                "scopes": info.get("scopes", []),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
