import pytest
from pydantic import ValidationError

from app.schemas import AssistantChatRequest, CustomerCreate, ProductPriceCreate


def test_assistant_message_is_trimmed():
    payload = AssistantChatRequest(message="  موجودی لوله را بررسی کن  ")
    assert payload.message == "موجودی لوله را بررسی کن"


def test_assistant_rejects_blank_message():
    with pytest.raises(ValidationError):
        AssistantChatRequest(message="   ")


def test_price_currency_is_normalized():
    payload = ProductPriceCreate(amount="125000.00", currency_code="irr")
    assert payload.currency_code == "IRR"


def test_price_rejects_invalid_currency():
    with pytest.raises(ValidationError):
        ProductPriceCreate(amount="100", currency_code="TOOMAN")


def test_customer_phone_is_normalized():
    payload = CustomerCreate(full_name="  مشتری  نمونه ", phone="+98 (912) 123-4567")
    assert payload.full_name == "مشتری نمونه"
    assert payload.phone == "+989121234567"


def test_customer_rejects_too_short_name():
    with pytest.raises(ValidationError):
        CustomerCreate(full_name="A")
