from decimal import Decimal

from app.services.assistant import (
    build_grounded_catalog_reply,
    build_llm_messages,
    extract_requested_quantity,
    extract_search_terms,
    normalize_search_text,
)


def test_search_term_extraction_keeps_product_terms():
    terms = extract_search_terms("سلام، لطفاً قیمت لوله ۵ اینچ را می‌فرمایید؟")
    assert "لوله" in terms
    assert "5" in terms
    assert "اینچ" in terms
    assert "قیمت" not in terms


def test_search_normalization_handles_arabic_letters_and_digits():
    assert normalize_search_text("كالاى ١٢٣") == "کالای 123"


def test_search_term_extraction_handles_arabic_digits_and_yeh():
    terms = extract_search_terms("قیمت لوله ٥ اینچ")
    assert terms == ["لوله", "5", "اینچ"]


def test_requested_quantity_is_not_used_as_a_product_search_term():
    assert extract_search_terms("برای ۱۰ عدد لوله موجودی دارید؟") == ["لوله"]
    assert extract_search_terms("برای ۵ عدد لوله ۵ اینچ موجودی دارید؟") == [
        "لوله", "5", "اینچ"
    ]


def test_requested_quantity_does_not_treat_product_dimensions_as_order_quantity():
    assert extract_requested_quantity("قیمت لوله ۵ اینچ چقدر است؟") is None
    assert extract_requested_quantity("برای ۱۰ عدد لوله موجودی دارید؟") == Decimal("10")


def test_requested_quantity_accepts_product_unit():
    assert extract_requested_quantity("۵ شاخه از این کالا می‌خواهم") == Decimal("5")


def test_system_prompt_contains_no_transaction_guarantees():
    messages = build_llm_messages([], "قیمت کالا چقدر است؟", "[]")
    assert messages[0]["role"] == "system"
    assert "سفارش ثبت نکنید" in messages[0]["content"]
    assert "چند کالای مشابه" in messages[0]["content"]
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


def test_ambiguous_product_match_asks_customer_to_choose():
    facts = [
        {
            "sku": "590-A",
            "name": "لوله فولادی 5 اینچ",
            "match_score": 25,
            "exact_match": False,
            "price": {"amount": "100", "currency_code": "IRT"},
            "stock_by_warehouse": [],
        },
        {
            "sku": "590-B",
            "name": "لوله گالوانیزه 5 اینچ",
            "match_score": 25,
            "exact_match": False,
            "price": {"amount": "200", "currency_code": "IRT"},
            "stock_by_warehouse": [],
        },
    ]

    reply = build_grounded_catalog_reply("قیمت لوله ۵ اینچ چقدر است؟", facts)

    assert reply is not None
    assert "چند کالا" in reply
    assert "590-A" in reply
    assert "590-B" in reply
    assert "قیمت ثبت‌شده" not in reply


def test_exact_match_wins_over_lower_scored_similar_products():
    facts = [
        {
            "sku": "590-A",
            "name": "لوله فولادی 5 اینچ",
            "match_score": 1000,
            "exact_match": True,
            "price": {"amount": "100", "currency_code": "IRT"},
            "stock_by_warehouse": [],
        },
        {
            "sku": "590-B",
            "name": "لوله گالوانیزه 5 اینچ",
            "match_score": 25,
            "exact_match": False,
            "price": {"amount": "200", "currency_code": "IRT"},
            "stock_by_warehouse": [],
        },
    ]

    reply = build_grounded_catalog_reply("قیمت لوله فولادی ۵ اینچ چقدر است؟", facts)

    assert reply is not None
    assert "لوله فولادی 5 اینچ" in reply
    assert "۱۰۰ تومان" in reply
    assert "۲۰۰ تومان" not in reply


def test_other_warehouse_is_checked_for_requested_quantity():
    facts = [{
        "sku": "590",
        "name": "لوله فولادی",
        "unit": "شاخه",
        "match_score": 1000,
        "exact_match": True,
        "price": {"amount": "233000", "currency_code": "IRT"},
        "stock_by_warehouse": [
            {
                "warehouse": "انبار تهران",
                "on_hand": "5",
                "reserved": "4",
                "available": "1",
                "status": "RECORDED",
            },
            {
                "warehouse": "انبار کرج",
                "on_hand": "12",
                "reserved": "2",
                "available": "10",
                "status": "RECORDED",
            },
        ],
    }]

    reply = build_grounded_catalog_reply(
        "برای ۵ عدد لوله فولادی قیمت و موجودی دارید؟", facts
    )

    assert reply is not None
    assert "انبار تهران" in reply
    assert "قابل فروش ۱ شاخه" in reply
    assert "انبار کرج" in reply
    assert "قابل فروش ۱۰ شاخه" in reply
    assert "برای مقدار درخواستی ۵ شاخه" in reply
    assert "انبار کرج به‌تنهایی کافی است" in reply


def test_shortage_across_all_recorded_warehouses_is_reported():
    facts = [{
        "sku": "590",
        "name": "لوله فولادی",
        "unit": "شاخه",
        "match_score": 1000,
        "exact_match": True,
        "price": None,
        "stock_by_warehouse": [
            {
                "warehouse": "انبار تهران",
                "on_hand": "5",
                "reserved": "1",
                "available": "4",
                "status": "RECORDED",
            },
            {
                "warehouse": "انبار کرج",
                "on_hand": "2",
                "reserved": "0",
                "available": "2",
                "status": "RECORDED",
            },
        ],
    }]

    reply = build_grounded_catalog_reply(
        "برای ۱۰ عدد لوله فولادی موجودی دارید؟", facts
    )

    assert reply is not None
    assert "مجموع" in reply
    assert "۶ شاخه" in reply
    assert "۴ شاخه کسری" in reply


def test_unknown_warehouse_is_not_counted_as_zero_when_reporting_shortage():
    facts = [{
        "sku": "590",
        "name": "لوله فولادی",
        "unit": "شاخه",
        "match_score": 1000,
        "exact_match": True,
        "price": None,
        "stock_by_warehouse": [
            {
                "warehouse": "انبار تهران",
                "on_hand": "5",
                "reserved": "1",
                "available": "4",
                "status": "RECORDED",
            },
            {
                "warehouse": "انبار کرج",
                "status": "NO_STOCK_RECORD",
            },
        ],
    }]

    reply = build_grounded_catalog_reply(
        "برای ۱۰ عدد لوله فولادی موجودی دارید؟", facts
    )

    assert reply is not None
    assert "مجموع موجودی قابل فروشِ ثبت‌شده ۴ شاخه" in reply
    assert "نمی‌توان کمبود قطعی کل مجموعه را اعلام کرد" in reply


def test_low_confidence_single_candidate_asks_for_confirmation():
    facts = [{
        "sku": "590-A",
        "name": "لوله فولادی 5 اینچ",
        "match_score": 5,
        "exact_match": False,
        "matched_term_count": 1,
        "query_term_count": 3,
        "price": {"amount": "100", "currency_code": "IRT"},
        "stock_by_warehouse": [],
    }]

    reply = build_grounded_catalog_reply("قیمت لوله ۵ اینچ چقدر است؟", facts)

    assert reply is not None
    assert "شناسایی قطعی کالا کافی نیست" in reply
    assert "قیمت ثبت‌شده" not in reply
