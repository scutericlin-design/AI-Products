#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/deployment/dist"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="$OUT_DIR/chixiao-alpha-tencent-$STAMP.tar.gz"
INCLUDE_DATA="${INCLUDE_DATA:-0}"

usage() {
  cat <<EOF
Usage: $0 [--include-data]

Options:
  --include-data      Include local data/ for first-time server migration.
  --no-include-data   Exclude data/ from the archive. This is the default.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --include-data)
      INCLUDE_DATA=1
      ;;
    --no-include-data)
      INCLUDE_DATA=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

mkdir -p "$OUT_DIR"

TAR_ARGS=(
  --disable-copyfile \
  --no-xattrs \
  --exclude=".git" \
  --exclude=".venv" \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude=".DS_Store" \
  --exclude="._*" \
  --exclude=".env" \
  --exclude="deployment/dist" \
)

if [ "$INCLUDE_DATA" != "1" ]; then
  TAR_ARGS+=(--exclude="data")
  echo "Packing code only; data/ is excluded." >&2
else
  echo "Packing code and local data/ for first-time migration." >&2
fi

COPYFILE_DISABLE=1 tar "${TAR_ARGS[@]}" -czf "$OUT_FILE" -C "$ROOT_DIR" .

echo "$OUT_FILE"
