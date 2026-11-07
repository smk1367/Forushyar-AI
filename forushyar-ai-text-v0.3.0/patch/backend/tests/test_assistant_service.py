from app.services.assistant import build_llm_messages, extract_search_terms


def test_search_term_extraction_keeps_product_terms():
    terms = extract_search_terms("سلام، لطفاً قیمت لوله ۵ اینچ را می‌فرمایید؟")
    assert "لوله" in terms
    assert "اینچ" in terms


def test_system_prompt_contains_no_transaction_guarantees():
    messages = build_llm_messages([], "قیمت کالا چقدر است؟", "[]")
    assert messages[0]["role"] == "system"
    assert "سفارش ثبت نکنید" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "قیمت کالا چقدر است؟"}
