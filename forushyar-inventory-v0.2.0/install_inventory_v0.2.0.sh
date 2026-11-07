#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(pwd)"
PATCH_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/patch"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$ROOT/backups/inventory-v0.2.0-$STAMP"

fail() { echo "ERROR: $*" >&2; exit 1; }

[[ -f "$ROOT/docker-compose.yml" ]] || fail "این اسکریپت را از ریشه پروژه (محل docker-compose.yml) اجرا کنید."
[[ -d "$ROOT/backend/app" ]] || fail "مسیر backend/app پیدا نشد."
[[ -d "$PATCH_DIR/backend" ]] || fail "پوشه patch همراه بسته پیدا نشد."

command -v docker >/dev/null 2>&1 || fail "Docker در PATH پیدا نشد."
command -v tar >/dev/null 2>&1 || fail "tar در PATH پیدا نشد."

mkdir -p "$BACKUP_DIR"
echo "[1/6] بررسی سرویس‌های فعلی"
docker compose ps >/dev/null

# Save source files before replacing them.
echo "[2/6] پشتیبان‌گیری از کد فعلی"
tar -czf "$BACKUP_DIR/backend-source-before.tar.gz" \
  backend/app backend/alembic/versions backend/tests backend/requirements.txt backend/alembic.ini

test -s "$BACKUP_DIR/backend-source-before.tar.gz" || fail "پشتیبان کد خالی است."

# Save a consistent PostgreSQL custom-format backup before applying the migration.
echo "[3/6] پشتیبان‌گیری از PostgreSQL"
DB_TMP="$BACKUP_DIR/database-before.dump.tmp"
DB_FILE="$BACKUP_DIR/database-before.dump"
if docker compose exec -T db sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl' \
  > "$DB_TMP"; then
  test -s "$DB_TMP" || fail "فایل پشتیبان دیتابیس خالی است."
  mv "$DB_TMP" "$DB_FILE"
else
  rm -f "$DB_TMP"
  fail "پشتیبان‌گیری دیتابیس شکست خورد؛ هیچ کدی جایگزین یا Migration اجرا نشد."
fi

# Copy only this release's files into the existing project tree.
echo "[4/6] نصب فایل‌های نسخه 0.2.0"
cp -a "$PATCH_DIR/backend/." "$ROOT/backend/"

# Build new image while the currently running API remains in place.
echo "[5/6] ساخت image جدید و اجرای Migration"
docker compose build api
docker compose run --rm --no-deps api alembic upgrade head

# Start/recreate API with the new image only after migration succeeds.
echo "[6/6] راه‌اندازی API جدید و بررسی وضعیت"
docker compose up -d api

echo
echo "Migration فعلی:"
docker compose run --rm --no-deps api alembic current

echo
echo "وضعیت کانتینرها:"
docker compose ps

echo
APP_PORT="${APP_PORT:-$(awk -F= '/^APP_PORT=/ {print $2; exit}' .env 2>/dev/null || true)}"
APP_PORT="${APP_PORT:-8000}"

echo "Health check:"
curl -fsS "http://127.0.0.1:${APP_PORT}/health/ready" || {
  echo "API آماده نیست. لاگ را با docker compose logs --tail=100 api بررسی کنید." >&2
  exit 1
}
echo

# In development mode, verify that all new routes are visible in OpenAPI.
if curl -fsS "http://127.0.0.1:${APP_PORT}/openapi.json" -o /tmp/forushyar-openapi-v020.json; then
  python3 - <<'PYOPENAPI'
import json
with open('/tmp/forushyar-openapi-v020.json', encoding='utf-8') as stream:
    paths = json.load(stream).get('paths', {})
required = [
    '/api/v1/inventory/receipts',
    '/api/v1/inventory/issues',
    '/api/v1/inventory/transfers',
    '/api/v1/inventory/reservations',
    '/api/v1/inventory/movements',
    '/api/v1/inventory/{sku}/movements',
]
missing = [path for path in required if path not in paths]
if missing:
    raise SystemExit('مسیرهای API یافت نشدند: ' + ', '.join(missing))
print('OpenAPI routes OK:', len(required), 'مسیر')
PYOPENAPI
else
  echo "OpenAPI در این محیط فعال نیست؛ بررسی مسیرها رد شد (در حالت production طبیعی است)."
fi

echo
echo "نصب نسخه 0.2.0 انجام شد. پشتیبان‌ها در: $BACKUP_DIR"
