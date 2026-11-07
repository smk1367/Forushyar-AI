# سامانه فروش و پشتیبانی هوشمند - اسکلت اجرایی نسخه 0.1

این مخزن پایه توسعه پروژه است. در حال حاضر API اولیه مدیریت کالا، انبار و اصلاح موجودی را همراه PostgreSQL و Redis فراهم می‌کند. این نسخه هنوز شامل SIP، مدل مکالمه، پرداخت واقعی، فاکتور قانونی، CRM کامل یا پنل وب نیست.

## پیش‌نیازها

- Debian 13 (Trixie)
- Docker Engine و افزونه Docker Compose
- حداقل 4 GB RAM برای توسعه ساده (ظرفیت تولید باید جداگانه برآورد شود)

## نصب Docker روی Debian 13

اسکریپت زیر مخزن رسمی Docker را اضافه و Engine و افزونه Compose را نصب می‌کند:

```bash
sudo bash scripts/install_docker_debian13.sh
```

قبل از انتشار سرویس‌ها، قوانین Firewall را بازبینی کنید. مستندات Docker هشدار می‌دهد که انتشار پورت کانتینر می‌تواند با قواعد UFW تداخل داشته باشد؛ در این اسکلت، API فقط روی loopback میزبان منتشر شده و DB/Redis اصلاً پورت میزبان ندارند.

## راه‌اندازی توسعه

```bash
cp .env.example .env
# رمز PostgreSQL و API_KEY را در .env جایگزین کنید.
docker compose up -d db redis
docker compose run --rm api alembic upgrade head
docker compose up -d api
```

یا اسکریپت زیر را اجرا کنید:

```bash
./scripts/bootstrap_dev.sh
```

پس از اجرا:

- سلامت: `http://127.0.0.1:8000/health/live`
- آمادگی DB و Redis: `http://127.0.0.1:8000/health/ready`
- مستند API در محیط توسعه: `http://127.0.0.1:8000/docs`

پورت API فقط روی `127.0.0.1` میزبان منتشر می‌شود. دیتابیس و Redis پورت عمومی ندارند.

## APIهای موجود

همه مسیرهای `/api/v1` فعلاً به هدر `X-API-Key` نیاز دارند. کلید نمونه صرفاً برای توسعه محلی است.

- `GET /api/v1/products`
- `POST /api/v1/products`
- `GET /api/v1/warehouses`
- `POST /api/v1/warehouses`
- `GET /api/v1/inventory/{sku}`
- `POST /api/v1/inventory/adjustments`

نمونه ایجاد کالا:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/products \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_LOCAL_API_KEY' \
  -d '{"sku":"SKU-1001","name":"کالای آزمایشی","unit":"عدد"}'
```

نمونه ایجاد انبار:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/warehouses \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_LOCAL_API_KEY' \
  -d '{"code":"WH-01","name":"انبار شماره یک","branch_name":"دفتر مرکزی"}'
```

برای ثبت موجودی اولیه، شناسه کالا و انبار برگشتی از API را در بدنه زیر قرار دهید:

```json
{
  "product_id": "UUID_OF_PRODUCT",
  "warehouse_id": "UUID_OF_WAREHOUSE",
  "quantity_delta": "25.000",
  "reason": "ثبت موجودی اولیه پس از شمارش",
  "idempotency_key": "opening-2026-000001",
  "notes": "ثبت آزمایشی"
}
```

```bash
curl -X POST http://127.0.0.1:8000/api/v1/inventory/adjustments \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_LOCAL_API_KEY' \
  -d @adjustment.json
```

## فرمان‌های مدیریتی

```bash
# مشاهده وضعیت سرویس‌ها
docker compose ps

# مشاهده لاگ‌ها
docker compose logs -f --tail=150

# اجرای migrationهای جدید
docker compose run --rm api alembic upgrade head

# اجرای تست‌ها
docker compose run --rm api pytest -q

# تهیه نسخه پشتیبان منطقی پایگاه داده
./scripts/backup_db.sh

# بازیابی مخرب است و فقط با تأیید صریح اجرا می‌شود
CONFIRM_RESTORE=YES ./scripts/restore_db.sh ./backups/smart-sales-db-YYYYMMDDTHHMMSSZ.dump

# توقف سرویس‌ها بدون حذف داده‌ها
docker compose down

# هشدار: حذف volumeها داده دیتابیس و Redis را پاک می‌کند؛ فقط در محیط آزمایشی.
# docker compose down -v
```

## الزامات پیش از تولید

1. جایگزینی کلیدهای نمونه با Secret Manager یا اسرار امن و یکتا.
2. پیاده‌سازی OAuth2/OIDC، MFA، RBAC و حساب‌های کاربری واقعی؛ API Key فعلی فقط برای شروع محلی است.
3. افزودن TLS، reverse proxy و محدودیت‌های شبکه.
4. انتقال backup به مقصد خارج از میزبان، رمزنگاری و آزمون دوره‌ای بازیابی؛ اسکریپت نسخه پشتیبان محلی جایگزین سیاست Off-site/PITR نیست.
5. افزودن import staging برای Excel/CSV، قیمت، مشتری، سفارش و پرداخت.
6. اتصال UCM6202 از طریق Asterisk و آزمایش SIP/RTP روی Firmware واقعی.
7. تست بار، تست امنیت و آزمون پذیرش عملیاتی.

شرح کامل معماری و نقشه فازها در `docs/ARCHITECTURE_FA.md` و سند Word تحویلی قرار دارد.
