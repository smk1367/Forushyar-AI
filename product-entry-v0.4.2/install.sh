#!/usr/bin/env bash
set -euo pipefail

BUNDLE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${1:-$(pwd)}"
ROOT="$(cd -- "$ROOT" && pwd)"

if [[ ! -f "$ROOT/backend/app/main.py" || ! -f "$ROOT/backend/app/services/assistant.py" ]]; then
  echo "ERROR: target is not the Forushyar-AI repository root: $ROOT" >&2
  exit 1
fi
for f in \
  "$BUNDLE_DIR/backend/app/api/product_entry.py" \
  "$BUNDLE_DIR/backend/app/web/product_entry.html" \
  "$BUNDLE_DIR/patch_sources.py"; do
  [[ -s "$f" ]] || { echo "ERROR: missing or empty bundle file: $f" >&2; exit 1; }
done

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$ROOT/backups/product-entry-$STAMP"
mkdir -p "$BACKUP" "$ROOT/backend/app/api" "$ROOT/backend/app/web"
for f in \
  backend/app/main.py \
  backend/app/services/assistant.py \
  README.fa.md \
  backend/app/api/product_entry.py \
  backend/app/web/product_entry.html; do
  if [[ -f "$ROOT/$f" ]]; then
    mkdir -p "$BACKUP/$(dirname "$f")"
    cp -a "$ROOT/$f" "$BACKUP/$f"
  fi
done

echo "[1/3] Installing product entry API and Persian web form..."
install -m 0644 "$BUNDLE_DIR/backend/app/api/product_entry.py" "$ROOT/backend/app/api/product_entry.py"
install -m 0644 "$BUNDLE_DIR/backend/app/web/product_entry.html" "$ROOT/backend/app/web/product_entry.html"

echo "[2/3] Registering routes and Toman interpretation..."
python3 "$BUNDLE_DIR/patch_sources.py" "$ROOT"

echo "[3/3] Verifying Python syntax and installed files..."
python3 -m compileall -q "$ROOT/backend/app"
test -s "$ROOT/backend/app/api/product_entry.py"
test -s "$ROOT/backend/app/web/product_entry.html"
grep -q "product_entry_api_router" "$ROOT/backend/app/main.py"
grep -q "product_entry_web_router" "$ROOT/backend/app/main.py"

echo
echo "Product-entry installation completed."
echo "API source: $(wc -c < "$ROOT/backend/app/api/product_entry.py") bytes"
echo "HTML form:  $(wc -c < "$ROOT/backend/app/web/product_entry.html") bytes"
echo "Backup: $BACKUP"
echo "Next: cd '$ROOT' && docker compose build api && docker compose up -d api"
echo "URL (through SSH tunnel): http://127.0.0.1:18000/admin/products"
