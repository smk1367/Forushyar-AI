# راه‌اندازی اتصال Grandstream UCM6202 در فروش‌یار

این ماژول قابلیت‌های تلفنی مخزن \`grandstream-U6202-CRM\` را در بک‌اند اصلی Forushyar-AI ادغام می‌کند. سرویس CRM قدیمی، پایگاه داده دوم و احراز هویت مستقل آن اجرا نمی‌شوند. تماس‌ها در PostgreSQL اصلی فروش‌یار ذخیره می‌شوند و در صورت تطبیق شماره با \`customers.phone\` به پرونده مشتری متصل می‌شوند.

## قابلیت‌های این مرحله

- دریافت و نرمال‌سازی CDR در قالب JSON، XML و CSV مخصوص TCP.
- همگام‌سازی صفحه‌بندی‌شده CDR با HTTP Digest Authentication و جلوگیری از رکورد تکراری بر اساس شناسه UCM.
- پایش کانال‌های زنده از طریق \`listUnBridgedChannels\` و \`listBridgedChannels\`.
- مشاهده، جست‌وجو و فیلتر تماس‌های ورودی، خروجی، داخلی و تماس‌های ازدست‌رفته.
- پیوند سابقه تماس با مشتری موجود در فروش‌یار.
- دریافت فایل ضبط با کنترل دسترسی و اعتبارسنجی مسیر فایل.
- تماس خروجی و تماس با داخلی از طریق \`dialOutbound\` و \`dialExtension\`. داخلی مبدا فقط از پروفایل کارمند واردشده خوانده می‌شود.
- ثبت نتیجه و یادداشت تماس.

## متغیرهای محیطی

مقادیر را در فایل محلی \`.env\` سرور تنظیم کنید. رمز واقعی را در Git commit نکنید.

\`\`\`dotenv
UCM_ENABLED=true
UCM_API_URL=https://PBX_IP:8089/api
UCM_API_USERNAME=forushyar_api_user
UCM_API_PASSWORD=CHANGE_ME

UCM_CDR_URL=https://PBX_IP:8443/cdrapi
UCM_RECORDING_URL=https://PBX_IP:8443/recapi
UCM_CDR_USERNAME=forushyar_cdr_user
UCM_CDR_PASSWORD=CHANGE_ME
UCM_VERIFY_TLS=true

UCM_SYNC_INTERVAL_SECONDS=60
UCM_LIVE_POLL_SECONDS=2
UCM_CDR_PAGE_SIZE=500
UCM_CDR_MAX_PAGES=20

# HTTP/HTTPS Real-time CDR callback (اختیاری)
UCM_WEBHOOK_USERNAME=forushyar_cdr_push
UCM_WEBHOOK_PASSWORD=CHANGE_ME

# TCP Real-time CDR receiver (اختیاری، allowlist اجباری)
UCM_TCP_LISTENER_ENABLED=false
UCM_TCP_HOST=0.0.0.0
UCM_TCP_PORT=10000
UCM_TCP_ALLOWED_IPS=["192.168.1.20"]
UCM_TCP_BIND_IP=192.168.1.10
UCM_TCP_HOST_PORT=10000
\`\`\`

برای TLS از گواهی معتبر یا CA مورداعتماد استفاده کنید. خاموش‌کردن بررسی TLS برای محیط عملیاتی توصیه نمی‌شود.

## تنظیمات UCM

1. در UCM، HTTPS API را فعال کنید و حساب کاربری مخصوص API با حداقل مجوزهای لازم بسازید.
2. برای CDR و Recording API حساب مجزا با HTTP Digest Authentication تنظیم کنید.
3. CDR API و Recording API را فعال کنید و آدرس‌ها، پورت‌ها و حساب‌ها را با مقادیر محیطی تطبیق دهید.
4. در CDR Real-time Output یکی از دو روش را انتخاب کنید:
   - HTTP/HTTPS: مقصد \`https://SERVER/api/v1/telephony/cdr-realtime\` باشد و Basic Auth با \`UCM_WEBHOOK_USERNAME\` و \`UCM_WEBHOOK_PASSWORD\` یکسان تنظیم شود.
   - TCP: مقدار \`UCM_TCP_LISTENER_ENABLED=true\` شود، IP دستگاه در \`UCM_TCP_ALLOWED_IPS\` قرار گیرد، و \`UCM_TCP_BIND_IP\` در فایل \`.env\` به IP داخلی سرور تغییر کند. فایروال باید پورت TCP را فقط برای IP مرکز تلفن باز کند.
5. در مدیریت کارکنان داشبورد، داخلی هر کارمند را ثبت کنید. این مقدار مبنای تماس خروجی است.

## مسیرهای API

مسیرهای کاربری نیازمند نشست معتبر فروش‌یار هستند:

- \`GET /api/v1/telephony/status\`
- \`GET /api/v1/telephony/live\`
- \`GET /api/v1/telephony/calls\`
- \`GET /api/v1/telephony/calls/{call_id}\`
- \`GET /api/v1/telephony/customers/{customer_id}/calls\`
- \`GET /api/v1/telephony/calls/{call_id}/recordings/{index}\`
- \`POST /api/v1/telephony/sync\` (ADMIN/MANAGER و CSRF)
- \`POST /api/v1/telephony/click-to-call\` (ADMIN/MANAGER/SALES و CSRF)
- \`POST /api/v1/telephony/calls/{call_id}/outcome\` (ADMIN/MANAGER/SALES و CSRF)
- \`POST /api/v1/telephony/cdr-realtime\` (Basic Auth اختصاصی webhook)

صفحه تلفن در مسیر \`/telephony\` در دسترس است.

## مهاجرت و آزمون

این نسخه migration با شناسه \`0008_grandstream_telephony\` اضافه می‌کند: ستون \`employees.phone_extension\` و جدول \`telephony_calls\` ساخته می‌شوند. migration داده‌های قبلی مشتریان، سفارش‌ها یا انبار را حذف یا بازنویسی نمی‌کند.

پس از بررسی Pull Request و گرفتن نسخه پشتیبان PostgreSQL، migration را با روال استاندارد پروژه اجرا کنید. آزمون تماس واقعی، ضبط، callback و TCP باید با دستگاه UCM6202 و firmware واقعی انجام شود؛ تست‌های CI جای آزمون عملیاتی PBX را نمی‌گیرند.

## محدودیت‌ها و نکات بهره‌برداری

- پاسخ موفق API تماس به‌تنهایی اثبات نمی‌کند که تماس به مقصد وصل شده یا مکالمه انجام شده است.
- ساختار CDR و نام فیلدها ممکن است با firmware یا تنظیمات PBX تفاوت داشته باشد؛ نمونه CDR واقعی باید بعد از راه‌اندازی تست شود.
- poller و listener داخل lifespan برنامه اجرا می‌شوند؛ در اجرای چند worker چند نمونه poller/listener ایجاد می‌شود. این نسخه باید با یک worker API اجرا شود یا در مرحله بعد worker به سرویس مستقل منتقل شود.
- ضبط مکالمه داده حساس است؛ HTTPS، نقش‌های دسترسی، محدودیت شبکه و سیاست نگهداری فایل‌ها باید رعایت شود.

## بررسی اتصال از سرور فروش‌یار (Issue #16)

پس از نصب نسخه حاوی این فرمان و تنظیم حساب API، روی سرور و از مسیر پروژه اجرا کنید:

```sh
docker compose exec -T api python -m app.cli.check_ucm
```

فرمان با تنظیمات همان کانتینر وارد API می‌شود و تعداد کانال‌های متصل و در انتظار اتصال را می‌خواند. صفر بودن تعداد کانال‌ها هنگام نبود تماس طبیعی است. خروجی فقط نام عملیات و تعداد یا نوع خطا را نشان می‌دهد. آزمون یک‌بار اجرا می‌شود و تماس یا همگام‌سازی CDR را آغاز نمی‌کند.

کد خروج صفر یعنی هر دو پاسخ کانال معتبر بودند. کد ۱ یعنی درخواست یا قالب پاسخ ناموفق بود. کد ۲ یعنی تنظیمات برنامه یا اتصال برای اجرای آزمون آماده نیست. برای ثبت نتیجه در Issue، زمان آزمون، نسخه دستگاه، کد خروج و دو خط وضعیت موجود را ثبت کنید. تطبیق شماره با مشتری، ثبت CDR و تماس واقعی هرکدام به آزمون جداگانه روی دستگاه نیاز دارند.
