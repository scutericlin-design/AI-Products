#!/usr/bin/env bash
set -euo pipefail

INCLUDE_DATA=0

usage() {
  cat <<EOF
Usage: $0 [--include-data] SERVER_IP [SERVER_USER] [REMOTE_DIR]

Examples:
  $0 1.2.3.4 root /opt/chixiao-alpha
  $0 --include-data 1.2.3.4 root /opt/chixiao-alpha

Options:
  --include-data      Include local data/ if this is the first deployment.
  --no-include-data   Exclude local data/. This is the default.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --include-data)
      INCLUDE_DATA=1
      shift
      ;;
    --no-include-data)
      INCLUDE_DATA=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

if [ $# -lt 1 ]; then
  usage >&2
  exit 1
fi

SERVER_IP="$1"
SERVER_USER="${2:-root}"
REMOTE_DIR="${3:-/opt/chixiao-alpha}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_ARGS=()
if [ -n "${SSH_KEY:-}" ]; then
  SSH_ARGS=(-i "$SSH_KEY" -o IdentitiesOnly=yes)
fi

PACK_ARGS=()
if [ "$INCLUDE_DATA" = "1" ]; then
  PACK_ARGS+=(--include-data)
fi

if [ ${#PACK_ARGS[@]} -gt 0 ]; then
  ARCHIVE="$(bash "$ROOT_DIR/deployment/pack_tencent.sh" "${PACK_ARGS[@]}")"
else
  ARCHIVE="$(bash "$ROOT_DIR/deployment/pack_tencent.sh")"
fi
REMOTE_ARCHIVE="/tmp/$(basename "$ARCHIVE")"

echo "Uploading $ARCHIVE to $SERVER_USER@$SERVER_IP:$REMOTE_ARCHIVE"
ssh "${SSH_ARGS[@]}" "$SERVER_USER@$SERVER_IP" "mkdir -p '$REMOTE_DIR'"
scp "${SSH_ARGS[@]}" "$ARCHIVE" "$SERVER_USER@$SERVER_IP:$REMOTE_ARCHIVE"

echo "Deploying on server..."
ssh "${SSH_ARGS[@]}" "$SERVER_USER@$SERVER_IP" "
  set -euo pipefail
  cd '$REMOTE_DIR'
  DATA_BACKUP_DIR=''
  if [ -e data/app.db ]; then
    DATA_BACKUP_DIR=\"/tmp/chixiao-alpha-data-\$(date +%Y%m%d_%H%M%S)\"
    mkdir -p \"\$DATA_BACKUP_DIR\"
    cp -a data \"\$DATA_BACKUP_DIR/data\"
    echo \"Existing server data backed up to \$DATA_BACKUP_DIR/data\"
  fi
  tar -xzf '$REMOTE_ARCHIVE' -C '$REMOTE_DIR'
  if [ -n \"\$DATA_BACKUP_DIR\" ]; then
    rm -rf data
    cp -a \"\$DATA_BACKUP_DIR/data\" data
    echo \"Existing server data restored after code update.\"
  fi
  if [ ! -f .env ]; then
    cp .env.example .env
  fi
  if [ \"\$(id -u)\" -eq 0 ]; then
    bash deployment/server_setup_ubuntu.sh
  else
    sudo bash deployment/server_setup_ubuntu.sh
  fi
  docker compose -f docker-compose.prod.yml --profile trading up -d --build
  docker compose -f docker-compose.prod.yml --profile trading ps
"

echo "Done. Open http://$SERVER_IP/"
