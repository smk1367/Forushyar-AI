# START FORUSHYAR FAST REPLIES V0.2.2

def _normalise_fa(text: str) -> str:
    text = str(text).replace('ي', 'ی').replace('ك', 'ک').replace('\u200c', ' ').lower()
    return re.sub(r'\s+', ' ', text).strip()


def _safe_known_answer(question: str) -> str | None:
    """Provide audited, non-LLM answers for frequently asked sales questions."""
    q = _normalise_fa(question)
    has_price = any(term in q for term in (
        'قیمت', 'لیست قیمت', 'نرخ', 'چنده', 'چقدر', 'فی رابیتس', 'فی واشر', 'فی هر',
    ))
    if has_price:
        return 'قیمت روز الان در این سامانه در دسترسم نیست. برای قیمت دقیق لطفاً از واحد فروش استعلام بگیرین.'

    if any(term in q for term in ('موجودی', 'موجوده', 'موجود است', 'توی انبار', 'در انبار')):
        return 'به موجودی لحظه ای انبار دسترسی ندارم. برای موجودی دقیق لطفاً از واحد فروش استعلام بگیرین.'

    products = any(term in q for term in (
        'چه محصول', 'چی تولید', 'چه تولید', 'چه کالایی', 'چه کالا',
        'چی می فروش', 'چی میفروش', 'محصولاتتون', 'محصولات شما',
        'چه چیزایی دار', 'چی دارین', 'چی دارید', 'کالاهای شما',
    ))
    if products:
        return 'ما رابیتس ۹ ستون، رابیتس ۱۳ ستون و واشر گالوانیزه نگهدارنده رابیتس داریم. درباره کدوم محصول راهنمایی می خواین؟'

    has_columns = any(term in q for term in ('۹ ستون', '9 ستون', 'نه ستون')) and any(
        term in q for term in ('۱۳ ستون', '13 ستون', 'سیزده ستون')
    )
    if has_columns and any(term in q for term in ('فرق', 'تفاوت', 'مقایسه', 'چه فرقی')):
        return 'تعداد ستون ها و مشخصات فنی این دو مدل متفاوته. برای انتخاب درست، نوع کار و وزن مورد نظرتون رو بگین.'

    if q in ('سلام', 'الو', 'سلام الو', 'درود', 'سلام سلام'):
        return 'سلام، به رابیتس لوتوس خوش اومدین. درباره رابیتس یا واشر گالوانیزه چه سؤالی دارین؟'

    return None


def answer(transcript: str, history: list[dict[str, str]]) -> str:
    canned = _safe_known_answer(transcript)
    if canned is not None:
        LOG.info('Known-answer shortcut used')
        return canned

    conversation = [{"role": "system", "content": SYSTEM_PROMPT}]
    conversation.extend(history[-4:])
    conversation.append({"role": "user", "content": transcript[:1600]})
    payload = {
        "model": MODEL,
        "messages": conversation,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "options": {
            "num_predict": 90,
            "num_ctx": 2048,
            "temperature": 0.2,
        },
    }
    try:
        body = request_json('http://ollama:11434/api/chat', payload, 45)
        if not isinstance(body.get('message'), dict):
            raise ValueError('Missing Ollama message')
        content = str(body['message'].get('content') or '').strip()
        content = re.sub(r'[`*_#<>]', '', content)
        content = re.sub(r'\s+', ' ', content).strip()
        if not content:
            raise ValueError('Empty Ollama response')
        return content[:240]
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        LOG.warning('Fast local model fallback: %s', type(exc).__name__)
        return 'برای این سؤال اطلاعات دقیق و تأیید شده ندارم. لطفاً از همکارای فروش راهنمایی بگیرین.'

# END FORUSHYAR FAST REPLIES V0.2.2
