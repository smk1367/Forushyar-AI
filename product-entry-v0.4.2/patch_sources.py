#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
main_path = root / "backend/app/main.py"
assistant_path = root / "backend/app/services/assistant.py"
readme_path = root / "README.fa.md"

main = main_path.read_text(encoding="utf-8")
import_line = "from app.api.product_entry import api_router as product_entry_api_router, web_router as product_entry_web_router\n"
if "product_entry_api_router" not in main:
    anchor = "from app.api.routes import router\n"
    if anchor not in main:
        raise SystemExit("ERROR: expected import anchor not found in backend/app/main.py")
    main = main.replace(anchor, import_line + anchor, 1)

include_anchor = "app.include_router(router)\n"
if include_anchor not in main:
    raise SystemExit("ERROR: expected app.include_router(router) not found in backend/app/main.py")
lines = []
if "app.include_router(product_entry_api_router)" not in main:
    lines.append("app.include_router(product_entry_api_router)\n")
if "app.include_router(product_entry_web_router)" not in main:
    lines.append("app.include_router(product_entry_web_router)\n")
if lines:
    main = main.replace(include_anchor, include_anchor + "".join(lines), 1)
main_path.write_text(main, encoding="utf-8")

assistant = assistant_path.read_text(encoding="utf-8")
needle = "فقط از اطلاعات واقعی بخش «داده‌های سامانه» برای اعلام مشخصات، قیمت و موجودی کالا استفاده کنید.\n"
addition = needle + "قیمت‌هایی که currency_code آن‌ها IRT است بر حسب تومان هستند؛ مبلغ را بدون تبدیل، به تومان اعلام کنید. کد IRR نشان‌دهنده ریال است.\n"
if "currency_code آن‌ها IRT" not in assistant:
    if needle not in assistant:
        raise SystemExit("ERROR: expected pricing instruction not found in assistant.py")
    assistant = assistant.replace(needle, addition, 1)
    assistant_path.write_text(assistant, encoding="utf-8")

if readme_path.exists():
    readme = readme_path.read_text(encoding="utf-8")
    marker = "## پنل ورود کالا\n"
    section = """## پنل ورود کالا

پس از اجرای نسخه جدید API، فرم فارسی ثبت کالا از نشانی زیر در همان میزبان در دسترس است:

`http://127.0.0.1:8000/admin/products`

در فرم می‌توان کالا، قیمت روز به تومان، انبار و موجودی اولیه را ثبت کرد. ثبت مشخصات کالا، قیمت و موجودی اولیه در یک تراکنش دیتابیس انجام می‌شود. اگر موجودی اولیه وارد نشود، سامانه موجودی را حدس نمی‌زند و رکورد موجودی ایجاد نمی‌کند. برای هر وزن یا تعداد ستون متفاوت، کد کالای مجزا وارد کنید. برای این بخش migration جدید لازم نیست؛ جداول قیمت و انبار از قبل در پروژه وجود دارند.

قیمت‌های این فرم با کد داخلی `IRT` و بر حسب تومان ذخیره می‌شوند؛ دستیار هوشمند نیز این کد را به‌عنوان تومان تفسیر می‌کند.

"""
    if marker not in readme:
        anchor = "## فرمان‌های مدیریتی\n"
        if anchor in readme:
            readme = readme.replace(anchor, section + anchor, 1)
            readme_path.write_text(readme, encoding="utf-8")

print("Source integration completed.")
