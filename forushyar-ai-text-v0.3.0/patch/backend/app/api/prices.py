from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.db.session import get_db
from app.models.product import Product
from app.models.product_price import ProductPrice
from app.schemas import ProductPriceCreate, ProductPriceRead

router = APIRouter(tags=["Product pricing"], dependencies=[Depends(require_api_key)])


@router.post("/products/{sku}/prices", response_model=ProductPriceRead, status_code=status.HTTP_201_CREATED)
async def set_product_price(sku: str, payload: ProductPriceCreate, session: AsyncSession = Depends(get_db)):
    product = await session.scalar(select(Product).where(Product.sku == sku).with_for_update())
    if product is None:
        raise HTTPException(status_code=404, detail="کالا یافت نشد")

    now = datetime.now(timezone.utc)
    active_prices = list((await session.scalars(
        select(ProductPrice).where(ProductPrice.product_id == product.id, ProductPrice.is_active.is_(True)).with_for_update()
    )).all())
    for old_price in active_prices:
        old_price.is_active = False
        old_price.effective_to = now

    price = ProductPrice(
        product_id=product.id,
        amount=payload.amount,
        currency_code=payload.currency_code.upper(),
        source=payload.source,
        notes=payload.notes,
        is_active=True,
    )
    session.add(price)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="ثبت قیمت با تعارض هم‌زمانی روبه‌رو شد؛ دوباره تلاش کنید") from exc
    await session.refresh(price)
    return price


@router.get("/products/{sku}/prices", response_model=list[ProductPriceRead])
async def list_product_prices(sku: str, session: AsyncSession = Depends(get_db)):
    product = await session.scalar(select(Product).where(Product.sku == sku))
    if product is None:
        raise HTTPException(status_code=404, detail="کالا یافت نشد")
    stmt = select(ProductPrice).where(ProductPrice.product_id == product.id).order_by(ProductPrice.effective_from.desc())
    return list((await session.scalars(stmt)).all())
