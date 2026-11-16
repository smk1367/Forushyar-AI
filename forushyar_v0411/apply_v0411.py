#!/usr/bin/env python3
from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"ERROR: {message}")


def backup(path: Path, project: Path, backup_root: Path) -> None:
    if path.exists():
        relative = path.relative_to(project)
        destination = backup_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: apply_v0411.py PROJECT_DIR PACKAGE_DIR")

    project = Path(sys.argv[1]).expanduser().resolve()
    package = Path(sys.argv[2]).expanduser().resolve()
    require((project / "backend/app/main.py").is_file(), f"پروژه در مسیر {project} پیدا نشد")
    require((project / "backend/app/api/product_entry.py").is_file(), "فایل product_entry.py در این پروژه پیدا نشد")
    require((project / "backend/app/web/product_entry.html").is_file(), "صفحه ورود کالا پیدا نشد")

    main_file = project / "backend/app/main.py"
    config_file = project / "backend/app/core/config.py"
    requirements_file = project / "backend/requirements.txt"
    html_file = project / "backend/app/web/product_entry.html"
    module_file = project / "backend/app/api/product_import.py"
    tests_file = project / "backend/tests/test_product_import.py"

    main_text = main_file.read_text(encoding="utf-8")
    config_text = config_file.read_text(encoding="utf-8")
    requirements_text = requirements_file.read_text(encoding="utf-8")
    html_text = html_file.read_text(encoding="utf-8")

    require(
        "from app.api.product_entry import api_router as product_entry_api_router, web_router as product_entry_web_router" in main_text,
        "ساختار main.py با نسخه مورد انتظار مطابقت ندارد؛ هیچ فایلی تغییر نکرد.",
    )
    require("app.include_router(product_entry_api_router)" in main_text, "محل اتصال product entry router در main.py پیدا نشد")
    require("نشانی این صفحه:" in html_text, "نقطه درج بخش Excel در صفحه کالا پیدا نشد")
    require("</style>" in html_text and "</body>" in html_text, "ساختار HTML صفحه کالا کامل نیست")
    require((package / "backend/app/api/product_import.py").is_file(), "ماژول جدید در بسته نصب وجود ندارد")
    require((package / "frontend/product_import_section.html").is_file(), "بخش رابط کاربری در بسته نصب وجود ندارد")
    require((package / "frontend/product_import.js").is_file(), "کد JavaScript در بسته نصب وجود ندارد")

    if module_file.exists():
        current = module_file.read_text(encoding="utf-8")
        incoming = (package / "backend/app/api/product_import.py").read_text(encoding="utf-8")
        require(current == incoming, "product_import.py از قبل وجود دارد و با نسخه این بسته یکسان نیست؛ برای جلوگیری از بازنویسی متوقف شدم.")
    if "PRODUCT_IMPORT_V0411_SECTION" not in html_text:
        require("نشانی این صفحه:" in html_text, "نشانگر محل افزودن بخش Excel پیدا نشد")
    if "PRODUCT_IMPORT_V0411_SCRIPT" not in html_text:
        require("</body>" in html_text, "محل افزودن JavaScript پیدا نشد")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = project / ".backups" / f"v0.4.11-before-excel-{timestamp}"
    backup_root.mkdir(parents=True, exist_ok=False)
    for path in (main_file, config_file, requirements_file, html_file, module_file, tests_file):
        backup(path, project, backup_root)

    # New backend module and regression tests.
    module_file.parent.mkdir(parents=True, exist_ok=True)
    incoming_module = (package / "backend/app/api/product_import.py").read_text(encoding="utf-8")
    if not module_file.exists():
        module_file.write_text(incoming_module, encoding="utf-8")
    tests_file.parent.mkdir(parents=True, exist_ok=True)
    incoming_tests = (package / "backend/tests/test_product_import.py").read_text(encoding="utf-8")
    if tests_file.exists():
        require(tests_file.read_text(encoding="utf-8") == incoming_tests, "تست product_import.py از قبل وجود دارد و متفاوت است؛ برای جلوگیری از بازنویسی متوقف شدم.")
    else:
        tests_file.write_text(incoming_tests, encoding="utf-8")

    # Register the API router exactly once.
    main_text = main_file.read_text(encoding="utf-8")
    import_line = "from app.api.product_import import api_router as product_import_api_router"
    if import_line not in main_text:
        anchor = "from app.api.product_entry import api_router as product_entry_api_router, web_router as product_entry_web_router"
        main_text = main_text.replace(anchor, anchor + "\n" + import_line, 1)
    include_line = "app.include_router(product_import_api_router)"
    if include_line not in main_text:
        anchor = "app.include_router(product_entry_api_router)"
        main_text = main_text.replace(anchor, anchor + "\n" + include_line, 1)
    main_file.write_text(main_text, encoding="utf-8")

    # Set the API release version.
    config_text = config_file.read_text(encoding="utf-8")
    if 'app_version: str = "0.4.11"' not in config_text:
        updated, count = re.subn(
            r'app_version\s*:\s*str\s*=\s*["\'][^"\']+["\']',
            'app_version: str = "0.4.11"',
            config_text,
            count=1,
        )
        require(count == 1, "تعریف app_version در config.py پیدا نشد؛ فایل را دستی بررسی کنید")
        config_file.write_text(updated, encoding="utf-8")

    # Add the Excel parser dependency.
    requirements_text = requirements_file.read_text(encoding="utf-8")
    if not re.search(r"(?im)^openpyxl\s*[<=>~]", requirements_text):
        requirements_file.write_text(requirements_text.rstrip() + "\nopenpyxl>=3.1.5,<4\n", encoding="utf-8")

    # Add the UI card, scoped CSS and browser-side interactions.
    html_text = html_file.read_text(encoding="utf-8")
    section_marker = "<!-- PRODUCT_IMPORT_V0411_SECTION -->"
    if section_marker not in html_text:
        section = (package / "frontend/product_import_section.html").read_text(encoding="utf-8")
        footer_marker = "  <div class=\"muted\">نشانی این صفحه:"
        require(footer_marker in html_text, "محل درج کارت Excel در HTML پیدا نشد")
        html_text = html_text.replace(footer_marker, section + "\n" + footer_marker, 1)

    css_marker = "/* PRODUCT_IMPORT_V0411_CSS */"
    if css_marker not in html_text:
        css = """
  /* PRODUCT_IMPORT_V0411_CSS */
  .import-table { width:100%; border-collapse:collapse; min-width:980px; font-size:12px; }
  .import-table th,.import-table td { padding:9px 10px; border-bottom:1px solid #e2e8f0; text-align:right; vertical-align:top; line-height:1.8; }
  .import-table th { position:sticky; top:0; background:#f1f5f9; z-index:1; white-space:nowrap; }
  .import-table tr:nth-child(even) { background:#fafcff; }
  .import-table .import-error { color:#b91c1c; min-width:250px; }
  .import-table .import-ok { color:#166534; white-space:nowrap; }
"""
        html_text = html_text.replace("</style>", css + "</style>", 1)

    script_marker = "// PRODUCT_IMPORT_V0411_SCRIPT"
    if script_marker not in html_text:
        js = (package / "frontend/product_import.js").read_text(encoding="utf-8")
        html_text = html_text.replace("</body>", "<script>\n" + js + "\n</script>\n</body>", 1)
    html_file.write_text(html_text, encoding="utf-8")

    print("\nنسخه 0.4.11 با موفقیت روی فایل‌های پروژه اعمال شد.")
    print(f"پوشه پشتیبان: {backup_root}")
    print("فایل‌های تغییرکرده/افزوده‌شده:")
    print("  backend/app/api/product_import.py (جدید)")
    print("  backend/app/main.py")
    print("  backend/app/core/config.py")
    print("  backend/requirements.txt")
    print("  backend/app/web/product_entry.html")
    print("  backend/tests/test_product_import.py (جدید)")
    print("\nاکنون ابتدا diff را بررسی کنید؛ سپس image را بسازید و سرویس API را بالا بیاورید.")


if __name__ == "__main__":
    main()
