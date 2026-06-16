#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/deployment/dist"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="$OUT_DIR/chixiao-alpha-tencent-$STAMP.tar.gz"

mkdir -p "$OUT_DIR"

tar \
  --exclude=".git" \
  --exclude=".venv" \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude=".DS_Store" \
  --exclude=".env" \
  --exclude="deployment/dist" \
  -czf "$OUT_FILE" \
  -C "$ROOT_DIR" \
  .

echo "$OUT_FILE"
