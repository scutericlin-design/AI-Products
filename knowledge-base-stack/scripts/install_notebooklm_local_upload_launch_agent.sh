#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CODEX_NODE="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
if [[ -z "${NODE_BIN:-}" ]]; then
  if [[ -x "$CODEX_NODE" ]]; then
    NODE_BIN="$CODEX_NODE"
  else
    NODE_BIN="$(command -v node || true)"
  fi
fi

if [[ -z "$NODE_BIN" ]]; then
  echo "Node.js is required. Set NODE_BIN=/path/to/node and retry."
  exit 1
fi

CACHE_DIR="${NOTEBOOKLM_LOCAL_CACHE_DIR:-$HOME/Documents/Hermes NotebookLM Source Packs}"
CHROME_PROFILE="${NOTEBOOKLM_CHROME_PROFILE_DIR:-$HOME/Library/Application Support/Hermes/NotebookLM Chrome Profile}"
CHROME_PORT="${NOTEBOOKLM_CHROME_PORT:-9222}"
UPLOAD_INTERVAL_SECONDS="${NOTEBOOKLM_UPLOAD_INTERVAL_SECONDS:-300}"
PLIST="$HOME/Library/LaunchAgents/com.hermes.notebooklm-local-upload.plist"
LOG_DIR="$HOME/Library/Logs/Hermes"

mkdir -p "$(dirname "$PLIST")" "$LOG_DIR" "$CACHE_DIR" "$CHROME_PROFILE"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.hermes.notebooklm-local-upload</string>
  <key>ProgramArguments</key>
  <array>
    <string>$NODE_BIN</string>
    <string>$ROOT_DIR/knowledge-base-stack/scripts/notebooklm_local_upload.js</string>
    <string>--cache-dir</string>
    <string>$CACHE_DIR</string>
    <string>--chrome-profile</string>
    <string>$CHROME_PROFILE</string>
    <string>--chrome-port</string>
    <string>$CHROME_PORT</string>
    <string>--limit</string>
    <string>5</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>NOTEBOOKLM_LOCAL_CACHE_DIR</key>
    <string>$CACHE_DIR</string>
    <key>NOTEBOOKLM_CHROME_PROFILE_DIR</key>
    <string>$CHROME_PROFILE</string>
    <key>NOTEBOOKLM_CHROME_PORT</key>
    <string>$CHROME_PORT</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>$UPLOAD_INTERVAL_SECONDS</integer>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/notebooklm-local-upload.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/notebooklm-local-upload.err.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" >/dev/null 2>&1 || true
launchctl load "$PLIST"
echo "Installed LaunchAgent: $PLIST"
echo "Cache dir: $CACHE_DIR"
echo "Chrome profile: $CHROME_PROFILE"
echo "Chrome remote debugging port: $CHROME_PORT"
