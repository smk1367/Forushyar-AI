# راهنمای ماژول عملیات انبار فورش‌یار – نسخه 0.2.0

این نسخه روی مدل اولیه محصول، انبار، مانده موجودی و دفتر گردش ساخته شده است. هیچ محصول یا موجودی آزمایشی به پایگاه داده اضافه نمی‌کند.

## قابلیت‌ها

- رسید ورود کالا: `POST /api/v1/inventory/receipts`
- حواله خروج کالا: `POST /api/v1/inventory/issues`
- اصلاح موجودی با مقدار علامت‌دار: `POST /api/v1/inventory/adjustments`
- انتقال یک کالا بین دو انبار: `POST /api/v1/inventory/transfers`
- ایجاد رزرو: `POST /api/v1/inventory/reservations`
- آزادسازی رزرو: `POST /api/v1/inventory/reservations/{reservation_id}/release`
- مصرف رزرو هنگام تحویل/فروش: `POST /api/v1/inventory/reservations/{reservation_id}/consume`
- گزارش مانده: `GET /api/v1/inventory/{sku}`
- تاریخچه یک کالا: `GET /api/v1/inventory/{sku}/movements`
- تاریخچه کل گردش‌ها: `GET /api/v1/inventory/movements`
- فهرست انتقال‌ها: `GET /api/v1/inventory/transfers`
- فهرست رزروها: `GET /api/v1/inventory/reservations`

همه مسیرهای `/api/v1` به هدر `X-API-Key` نیاز دارند.

## معنای فیلدهای موجودی

- `on_hand`: موجودی فیزیکی ثبت‌شده در انبار.
- `reserved`: بخشی از موجودی که برای سفارش/مشتری رزرو شده است.
- `available`: موجودی آزاد و قابل فروش؛ برابر `on_hand - reserved`.
- `quantity_delta`: تغییر موجودی فیزیکی در دفتر گردش.
- `reserved_delta`: تغییر مقدار رزروشده در دفتر گردش.

همه مقادیر مقداری با دقت سه رقم اعشار ذخیره می‌شوند. مقدار رسید، حواله، انتقال و رزرو باید مثبت باشد. در حواله و انتقال، سامانه موجودی آزاد را کنترل می‌کند؛ موجودی رزروشده قابل خروج یا انتقال نیست.

## نمونه درخواست‌ها

در مثال‌ها، شناسه‌های UUID باید از پاسخ واقعی API گرفته شوند. کلید API را داخل فایل یا خط فرمانی که در تاریخچه ذخیره می‌شود قرار ندهید.

### 1. ایجاد کالا

`POST /api/v1/products`

```json
{
  "sku": "SKU-001",
  "name": "نمونه کالا",
  "description": "مشخصات کالا",
  "unit": "عدد"
}
```

### 2. رسید ورود

`POST /api/v1/inventory/receipts`

```json
{
  "product_id": "<PRODUCT_UUID>",
  "warehouse_id": "<WAREHOUSE_UUID>",
  "quantity": "100.000",
  "reason": "رسید خرید",
  "idempotency_key": "receipt-po-2026-0001",
  "reference_type": "PURCHASE",
  "reference_id": "PO-2026-0001",
  "notes": "نمونه ساختار؛ قبل از اجرا شناسه و اطلاعات واقعی را جایگزین کنید"
}
```

### 3. حواله خروج

`POST /api/v1/inventory/issues`

```json
{
  "product_id": "<PRODUCT_UUID>",
  "warehouse_id": "<WAREHOUSE_UUID>",
  "quantity": "5.000",
  "reason": "خروج بابت ارسال سفارش",
  "idempotency_key": "issue-order-2026-0001",
  "reference_type": "ORDER",
  "reference_id": "ORDER-2026-0001"
}
```

### 4. انتقال بین دو انبار

`POST /api/v1/inventory/transfers`

```json
{
  "product_id": "<PRODUCT_UUID>",
  "from_warehouse_id": "<SOURCE_WAREHOUSE_UUID>",
  "to_warehouse_id": "<DEST_WAREHOUSE_UUID>",
  "quantity": "10.000",
  "reason": "انتقال بین انبارها",
  "idempotency_key": "transfer-2026-0001"
}
```

هر درخواست انتقال در این نسخه برای یک کالا است. کاهش موجودی مبدأ، افزایش موجودی مقصد، ثبت دو ردیف گردش و ثبت سربرگ انتقال در یک تراکنش دیتابیس انجام می‌شود؛ یا همه با هم ثبت می‌شوند یا هیچ‌کدام.

### 5. رزرو موجودی

`POST /api/v1/inventory/reservations`

```json
{
  "product_id": "<PRODUCT_UUID>",
  "warehouse_id": "<WAREHOUSE_UUID>",
  "quantity": "3.000",
  "reason": "رزرو برای سفارش مشتری",
  "idempotency_key": "reserve-order-2026-0001",
  "reference_type": "ORDER",
  "reference_id": "ORDER-2026-0001"
}
```

### 6. آزادسازی رزرو

`POST /api/v1/inventory/reservations/<RESERVATION_UUID>/release`

```json
{
  "reason": "لغو سفارش",
  "idempotency_key": "release-order-2026-0001"
}
```

آزادسازی فقط مقدار `reserved` را کم می‌کند و موجودی فیزیکی `on_hand` تغییر نمی‌کند.

### 7. مصرف رزرو

`POST /api/v1/inventory/reservations/<RESERVATION_UUID>/consume`

```json
{
  "reason": "تحویل کالا به مشتری",
  "idempotency_key": "consume-order-2026-0001"
}
```

مصرف رزرو هم‌زمان `on_hand` و `reserved` را به اندازه مقدار رزرو کم می‌کند.

## کلید جلوگیری از ثبت تکراری

برای هر عملیات تغییردهنده باید `idempotency_key` یکتا و حداقل ۸ کاراکتری ارسال شود. در ارسال مجدد همان درخواست، سامانه نباید اثر مالی/انبارداری را دوباره اعمال کند. استفاده از همان کلید برای عملیات یا اطلاعات متفاوت با پاسخ `409 Conflict` رد می‌شود.

## نکات عملیاتی و محدودیت‌های فعلی

1. هر انتقال در نسخه 0.2.0 شامل یک کالا است؛ انتقال چندقلمی در توسعه بعدی افزوده می‌شود.
2. این نسخه سیستم کاربران و نقش‌ها ندارد؛ دفتر گردش نوع عملیات، مقدار، دلیل، ارجاع و زمان را ذخیره می‌کند، اما هویت کارمند را ثبت نمی‌کند. ثبت عامل انسانی پس از پیاده‌سازی کاربران و احراز هویت فردی افزوده می‌شود.
3. این نسخه فایل Excel/CSV یا نرم‌افزار انبار را هنوز متصل نمی‌کند. درگاه ورود اطلاعات در فاز بعد طراحی خواهد شد.
4. این API جایگزین تأیید پرداخت یا ثبت نهایی سفارش نیست؛ اتصال فروش و پرداخت در مرحله جداگانه انجام می‌شود.
5. قبل از ثبت هر ورودی واقعی، موجودی اولیه باید با فایل/منبع معتبر انبار تطبیق و تأیید شود.
