from __future__ import annotations

import hashlib
import secrets
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.db.session import get_db
from app.models.inventory import InventoryBalance, StockMovement
from app.models.product import Product
from app.models.product_price import ProductPrice
from app.models.warehouse import Warehouse
from app.services.product_import import MAX_IMPORT_BYTES, build_product_import_template, parse_product_workbook, validate_product_import_rows

router = APIRouter(prefix="/api/v1/products/import", tags=["Product Excel import"], dependencies=[Depends(require_api_key)])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def read_upload(file: UploadFile):
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(415, "فقط فایل XLSX پذیرفته می‌شود")
    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "حجم فایل نباید بیشتر از ۵ مگابایت باشد")
    if not content:
        raise HTTPException(400, "فایل خالی است")
    try:
        rows = parse_product_workbook(content)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return content, hashlib.sha256(content).hexdigest(), rows


async def validate_db(session: AsyncSession, rows):
    skus = sorted({r["sku"].casefold() for r in rows if r.get("sku")})
    existing = set()
    if skus:
        existing = set((await session.scalars(select(Product.sku).where(func.lower(Product.sku).in_(skus)))).all())
    codes = sorted({r["warehouse_code"].casefold() for r in rows if r.get("warehouse_code")})
    warehouses = {}
    if codes:
        found = (await session.scalars(select(Warehouse).where(func.lower(Warehouse.code).in_(codes)))).all()
        warehouses = {w.code.casefold(): w.is_active for w in found}
    return validate_product_import_rows(rows, existing, warehouses)


@router.get("/template")
async def template():
    return StreamingResponse(BytesIO(build_product_import_template()), media_type=XLSX,
        headers={"Content-Disposition": 'attachment; filename="forushyar-products-template.xlsx"', "Cache-Control": "no-store"})


@router.post("/preview")
async def preview(file: UploadFile = File(...), session: AsyncSession = Depends(get_db)):
    try:
        _, digest, rows = await read_upload(file)
        checked = await validate_db(session, rows)
        return {"sha256": digest, **checked}
    finally:
        await file.close()


@router.post("/commit", status_code=status.HTTP_201_CREATED)
async def commit(file: UploadFile = File(...), preview_sha256: str = Form(..., min_length=64, max_length=64),
                 session: AsyncSession = Depends(get_db)):
    try:
        _, digest, rows = await read_upload(file)
        if not secrets.compare_digest(digest, preview_sha256.lower()):
            raise HTTPException(409, "فایل با پیش‌نمایش تأییدشده یکسان نیست؛ دوباره پیش‌نمایش بگیرید")
        checked = await validate_db(session, rows)
        if not checked["valid"]:
            raise HTTPException(422, detail={"message": "فایل دارای خطاست؛ هیچ داده‌ای ثبت نشد", **checked})
        codes = sorted({r["warehouse_code"].casefold() for r in rows if r.get("warehouse_code")})
        found = (await session.scalars(select(Warehouse).where(func.lower(Warehouse.code).in_(codes)).with_for_update())).all() if codes else []
        warehouse_map = {w.code.casefold(): w for w in found if w.is_active}
        groups = {}
        for row in rows: groups.setdefault(row["sku"], []).append(row)
        balances = movements = zero_balances = 0
        try:
            for sku, group in groups.items():
                first = group[0]
                product = Product(sku=sku, name=first["name"], description=first["description"], unit=first["unit"], is_active=True)
                session.add(product)
                await session.flush()
                if first["price_toman"] is not None:
                    session.add(ProductPrice(product_id=product.id, amount=Decimal(first["price_toman"]),
                        currency_code="IRT", source="EXCEL_IMPORT", notes=first["notes"], is_active=True))
                for row in group:
                    if row["opening_quantity"] is None: continue
                    warehouse = warehouse_map.get(row["warehouse_code"].casefold())
                    if warehouse is None: raise HTTPException(409, f"انبار ردیف {row['row_number']} دیگر فعال نیست")
                    quantity = Decimal(row["opening_quantity"])
                    session.add(InventoryBalance(product_id=product.id, warehouse_id=warehouse.id, on_hand=quantity, reserved=Decimal("0")))
                    balances += 1
                    if quantity > 0:
                        session.add(StockMovement(product_id=product.id, warehouse_id=warehouse.id, movement_type="OPENING",
                            quantity_delta=quantity, reserved_delta=Decimal("0"), reason="موجودی اولیه از ورود گروهی Excel",
                            idempotency_key=f"PRODUCT-IMPORT:{product.id}:{warehouse.id}", reference_type="PRODUCT_IMPORT",
                            reference_id=digest, notes=row["notes"]))
                        movements += 1
                    else: zero_balances += 1
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(409, "تعارض دیتابیس؛ کل عملیات لغو شد و هیچ بخشی ثبت نشد") from exc
        except Exception:
            await session.rollback()
            raise
        return {"status": "committed", "file_sha256": digest, "products_created": len(groups),
                "balances_created": balances, "movements_created": movements,
                "zero_balances_without_movement": zero_balances, "rows_processed": len(rows)}
    finally:
        await file.close()
