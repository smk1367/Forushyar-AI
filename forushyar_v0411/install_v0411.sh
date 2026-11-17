#!/usr/bin/env bash
set -Eeuo pipefail

PACKAGE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${1:-$HOME/forushyar-AI}"

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "پوشه پروژه پیدا نشد: $PROJECT_DIR" >&2
  echo "روش استفاده: bash install_v0411.sh /مسیر/forushyar-AI" >&2
  exit 1
fi

python3 "$PACKAGE_DIR/apply_v0411.py" "$PROJECT_DIR" "$PACKAGE_DIR"

if command -v git >/dev/null 2>&1 && git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo
  echo "===== GIT DIFF CHECK ====="
  git -C "$PROJECT_DIR" diff --check || true
  echo
  echo "===== GIT STATUS ====="
  git -C "$PROJECT_DIR" status --short
fi
