#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(pwd)"
PATCH_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/patch"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$ROOT_DIR/backups/ai-text-v0.3.0-$STAMP"

fail() { echo "ERROR: $*" >&2; exit 1; }

[[ -f "$ROOT_DIR/docker-compose.yml" ]] || fail "این اسکریپت را از ریشه پروژه، کنار docker-compose.yml اجرا کنید."
[[ -d "$ROOT_DIR/backend/app" ]] || fail "پوشه backend/app پیدا نشد."
[[ -d "$PATCH_DIR/backend" ]] || fail "پوشه patch همراه بسته پیدا نشد."
command -v docker >/dev/null 2>&1 || fail "Docker در PATH پیدا نشد."
command -v tar >/dev/null 2>&1 || fail "tar در PATH پیدا نشد."

mkdir -p "$BACKUP_DIR"
echo "[1/7] بررسی سرویس‌ها"
docker compose ps >/dev/null

echo "[2/7] پشتیبان‌گیری از کد فعلی"
tar -czf "$BACKUP_DIR/backend-source-before.tar.gz" \
  backend/app backend/alembic/versions backend/tests backend/requirements.txt backend/alembic.ini
test -s "$BACKUP_DIR/backend-source-before.tar.gz" || fail "پشتیبان کد ساخته نشد."

echo "[3/7] پشتیبان‌گیری از PostgreSQL"
DB_TMP="$BACKUP_DIR/database-before.dump.tmp"
DB_FILE="$BACKUP_DIR/database-before.dump"
if docker compose exec -T db sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl' > "$DB_TMP"; then
  test -s "$DB_TMP" || fail "پشتیبان دیتابیس خالی است."
  mv "$DB_TMP" "$DB_FILE"
else
  rm -f "$DB_TMP"
  fail "پشتیبان‌گیری دیتابیس شکست خورد؛ کد تغییر نکرد و Migration اجرا نشد."
fi

echo "[4/7] کپی فایل‌های نسخه 0.3.0"
cp -a "$PATCH_DIR/backend/." "$ROOT_DIR/backend/"

echo "[5/7] ساخت image جدید"
docker compose build api

echo "[6/7] اجرای Migration افزایشی 0003"
docker compose run --rm --no-deps api alembic upgrade head

echo "[7/7] راه‌اندازی API جدید"
docker compose up -d api

APP_PORT="$(sed -n 's/^APP_PORT=//p' .env 2>/dev/null | head -n1 | tr -d '"\047[:space:]' || true)"
APP_PORT="${APP_PORT:-8000}"
READY=0
for attempt in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${APP_PORT}/health/ready" >/tmp/forushyar-ready-v030.json; then
    READY=1
    break
  fi
  sleep 3
done
[[ "$READY" == "1" ]] || fail "API آماده نشد؛ docker compose logs --tail=100 api را بررسی کنید."

docker compose run --rm --no-deps api alembic current

if curl -fsS "http://127.0.0.1:${APP_PORT}/openapi.json" -o /tmp/forushyar-openapi-v030.json; then
  python3 - <<'PY'
import json
with open('/tmp/forushyar-openapi-v030.json', encoding='utf-8') as stream:
    paths = json.load(stream).get('paths', {})
required = [
    '/api/v1/customers',
    '/api/v1/conversations',
    '/api/v1/conversations/{conversation_id}/messages',
    '/api/v1/products/{sku}/prices',
    '/api/v1/assistant/status',
    '/api/v1/assistant/chat',
]
missing = [path for path in required if path not in paths]
if missing:
    raise SystemExit('مسیرهای جدید در OpenAPI پیدا نشدند: ' + ', '.join(missing))
print('AI/CRM OpenAPI routes OK:', len(required))
PY
else
  echo "OpenAPI در محیط production غیرفعال است؛ مسیرها از طریق health و لاگ بررسی شدند."
fi

echo
echo "وضعیت کانتینرها:"
docker compose ps
echo
echo "وضعیت موتور هوش مصنوعی (بدون نمایش کلید):"
python3 - "$APP_PORT" <<'PYSTATUS'
import json, sys
from pathlib import Path
from urllib.request import Request, urlopen
port = sys.argv[1]
env = {}
for line in Path('.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
req = Request(f'http://127.0.0.1:{port}/api/v1/assistant/status', headers={'X-API-Key': env['API_KEY']})
with urlopen(req, timeout=5) as response:
    print(json.dumps(json.loads(response.read()), ensure_ascii=False))
PYSTATUS
echo
echo "نصب نسخه 0.3.0 انجام شد. پشتیبان‌ها: $BACKUP_DIR"
echo "توجه: .env دست‌نخورده مانده است؛ تا زمان تنظیم LLM_BASE_URL و LLM_MODEL، مسیر chat عمداً 503 می‌دهد."
