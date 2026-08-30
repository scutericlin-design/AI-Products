#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${HERMES_KNOWLEDGE_SECRET:-}" && -z "${KNOWLEDGE_COMMAND_SECRET:-}" ]]; then
  echo "Set HERMES_KNOWLEDGE_SECRET or KNOWLEDGE_COMMAND_SECRET before installing."
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT_PYTHON="$ROOT_DIR/.venv/bin/python"
CODEX_PYTHON="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x "$PROJECT_PYTHON" ]]; then
    PYTHON_BIN="$PROJECT_PYTHON"
  elif [[ -x "$CODEX_PYTHON" ]]; then
    PYTHON_BIN="$CODEX_PYTHON"
  else
    PYTHON_BIN="$(command -v python3 || true)"
  fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python 3 is required. Set PYTHON_BIN=/path/to/python3 and retry."
  exit 1
fi

CLOUD_BASE_URL="${KNOWLEDGE_CLOUD_BASE_URL:-http://182.254.227.131}"
CACHE_DIR="${NOTEBOOKLM_LOCAL_CACHE_DIR:-$HOME/Documents/Hermes NotebookLM Source Packs}"
SYNC_DRIVE="${NOTEBOOKLM_SYNC_DRIVE:-true}"
DRIVE_FOLDER_ID="${GOOGLE_DRIVE_FOLDER_ID:-}"
GOOGLE_OAUTH_TOKEN_FILE="${GOOGLE_OAUTH_TOKEN_FILE:-$HOME/Library/Application Support/Hermes/google-oauth-token.json}"
PLIST="$HOME/Library/LaunchAgents/com.hermes.notebooklm-local-sync.plist"
LOG_DIR="$HOME/Library/Logs/Hermes"

mkdir -p "$(dirname "$PLIST")" "$LOG_DIR" "$CACHE_DIR"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.hermes.notebooklm-local-sync</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_BIN</string>
    <string>$ROOT_DIR/knowledge-base-stack/scripts/notebooklm_local_sync.py</string>
    <string>--watch</string>
    <string>--interval-seconds</string>
    <string>300</string>
    <string>--cloud-base-url</string>
    <string>$CLOUD_BASE_URL</string>
    <string>--cache-dir</string>
    <string>$CACHE_DIR</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HERMES_KNOWLEDGE_SECRET</key>
    <string>${HERMES_KNOWLEDGE_SECRET:-$KNOWLEDGE_COMMAND_SECRET}</string>
    <key>NOTEBOOKLM_SYNC_DRIVE</key>
    <string>$SYNC_DRIVE</string>
    <key>GOOGLE_DRIVE_FOLDER_ID</key>
    <string>$DRIVE_FOLDER_ID</string>
    <key>GOOGLE_OAUTH_TOKEN_FILE</key>
    <string>$GOOGLE_OAUTH_TOKEN_FILE</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/notebooklm-local-sync.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/notebooklm-local-sync.err.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" >/dev/null 2>&1 || true
launchctl load "$PLIST"
echo "Installed LaunchAgent: $PLIST"
echo "Cache dir: $CACHE_DIR"
