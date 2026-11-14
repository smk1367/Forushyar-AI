from app.services.assistant import (
    build_grounded_catalog_reply,
    build_llm_messages,
    extract_search_terms,
)


def test_search_term_extraction_keeps_product_terms():
    terms = extract_search_terms("سلام، لطفاً قیمت لوله ۵ اینچ را می‌فرمایید؟")
    assert "لوله" in terms
    assert "اینچ" in terms


def test_system_prompt_contains_no_transaction_guarantees():
    messages = build_llm_messages([], "قیمت کالا چقدر است؟", "[]")
    assert messages[0]["role"] == "system"
    assert "سفارش ثبت نکنید" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "قیمت کالا چقدر است؟"}



def test_missing_inventory_record_is_unknown_not_zero():
    facts = [{
        "sku": "590",
        "name": "کالای آزمایشی",
        "unit": "عدد",
        "price": None,
        "stock_by_warehouse": [{
            "warehouse": "تهران - آهن مکان",
            "warehouse_code": "WH-01",
            "status": "NO_STOCK_RECORD",
        }],
    }]

    reply = build_grounded_catalog_reply("موجودی کالای 590 در انبار چقدر است؟", facts)

    assert reply is not None
    assert "مقدار واقعی نامشخص است" in reply
    assert "صفر فرض نمی‌شود" in reply
    assert "قابل فروش ۰" not in reply


def test_recorded_inventory_reports_physical_reserved_and_available_values():
    facts = [{
        "sku": "590",
        "name": "کالای آزمایشی",
        "unit": "شاخه",
        "price": None,
        "stock_by_warehouse": [{
            "warehouse": "تهران - آهن مکان",
            "on_hand": "12.000",
            "reserved": "3.000",
            "available": "9.000",
            "status": "RECORDED",
        }],
    }]

    reply = build_grounded_catalog_reply("موجودی کالای 590 را بگو", facts)

    assert reply is not None
    assert "موجودی فیزیکی ۱۲" in reply
    assert "رزروشده ۳" in reply
    assert "قابل فروش ۹ شاخه" in reply


def test_irt_price_is_reported_in_toman_from_database_value():
    facts = [{
        "sku": "590",
        "name": "کالای آزمایشی",
        "unit": "عدد",
        "price": {"amount": "233000.00", "currency_code": "IRT"},
        "stock_by_warehouse": [],
    }]

    reply = build_grounded_catalog_reply("قیمت کالای 590 چقدر است؟", facts)

    assert reply is not None
    assert "۲۳۳٬۰۰۰ تومان" in reply


def test_non_catalog_question_is_left_for_language_model():
    assert build_grounded_catalog_reply("سلام، حالت چطوره؟", []) is None


def test_catalog_stock_question_with_no_match_does_not_invent_stock():
    reply = build_grounded_catalog_reply("موجودی کالای ناشناس چقدر است؟", [])

    assert reply is not None
    assert "کالای منطبقی پیدا نشد" in reply
    assert "صفر" not in reply



def test_recorded_zero_is_distinct_from_missing_inventory_record():
    facts = [{
        "sku": "590",
        "name": "کالای آزمایشی",
        "unit": "عدد",
        "price": None,
        "stock_by_warehouse": [{
            "warehouse": "تهران - آهن مکان",
            "on_hand": "0.000",
            "reserved": "0.000",
            "available": "0.000",
            "status": "RECORDED",
        }],
    }]

    reply = build_grounded_catalog_reply("موجودی کالای 590 چقدر است؟", facts)

    assert reply is not None
    assert "موجودی فیزیکی ۰" in reply
    assert "قابل فروش ۰ عدد" in reply
    assert "رکورد موجودی ثبت نشده" not in reply
