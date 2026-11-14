#!/usr/bin/env bash
set -euo pipefail

ROOT="$(pwd)"
if [[ ! -f "$ROOT/backend/app/main.py" || ! -f "$ROOT/backend/app/services/assistant.py" ]]; then
  echo "ERROR: Run this script from the Forushyar-AI repository root."
  exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$ROOT/backups/product-entry-$STAMP"
mkdir -p "$BACKUP_DIR" "$ROOT/backend/app/api" "$ROOT/backend/app/web"
cp "$ROOT/backend/app/main.py" "$BACKUP_DIR/main.py"
cp "$ROOT/backend/app/services/assistant.py" "$BACKUP_DIR/assistant.py"
cp "$ROOT/README.fa.md" "$BACKUP_DIR/README.fa.md" 2>/dev/null || true

echo "[1/4] Writing product registration API..."
cat > "$ROOT/backend/app/api/product_entry.py" <<'PY'
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, condecimal, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_api_key
from app.db.session import get_db
from app.models.inventory import InventoryBalance, StockMovement
from app.models.product import Product
from app.models.product_price import ProductPrice
from app.models.warehouse import Warehouse
from app.schemas import InventoryView, ProductPriceRead, ProductRead

NonNegativeQuantity = condecimal(max_digits=18, decimal_places=3, ge=0)


class ProductEntryCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=240)
    description: str | None = None
    unit: str = Field(default="عدد", min_length=1, max_length=24)
    # The entry screen accepts Toman. IRT is this application's internal
    # three-letter code for Toman (not the ISO code for Iranian Rial).
    price_toman: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    warehouse_id: UUID | None = None
    # None means no stock count was supplied; 0 means a confirmed zero balance.
    opening_quantity: NonNegativeQuantity | None = None
    notes: str | None = None

    @field_validator("sku", "name", "unit")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("این فیلد نباید خالی باشد")
        return value

    @model_validator(mode="after")
    def validate_opening_stock(self):
        if self.opening_quantity is not None and self.warehouse_id is None:
            raise ValueError("برای ثبت موجودی اولیه، انتخاب انبار الزامی است")
        return self


class ProductEntryRead(BaseModel):
    product: ProductRead
    price: ProductPriceRead | None = None
    inventory: InventoryView | None = None


api_router = APIRouter(
    prefix="/api/v1/products",
    tags=["Product entry"],
    dependencies=[Depends(require_api_key)],
)
web_router = APIRouter(tags=["Product entry UI"])

WEB_FILE = Path(__file__).resolve().parents[1] / "web" / "product_entry.html"


@web_router.get("/admin/products", response_class=HTMLResponse, include_in_schema=False)
async def product_entry_page():
    return HTMLResponse(
        content=WEB_FILE.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@api_router.post(
    "/entry",
    response_model=ProductEntryRead,
    status_code=status.HTTP_201_CREATED,
)
