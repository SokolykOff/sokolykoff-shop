"""AI-ядро магазина: семантический поиск, саммаризатор отзывов и консультант.

Архитектура двухрежимная:

1. Real LLM API — если задан ``OPENAI_API_KEY`` / ``GEMINI_API_KEY`` (или легаси ``AI_API_KEY``
   для любого OpenAI-совместимого провайдера), запросы уходят через чистый HTTP-клиент
   (``urllib``) с жёстким таймаутом 5-8 секунд и полной обработкой сетевых ошибок.
2. Smart Local Fallback — если ключа нет (демо-стенд) или внешний API ответил ошибкой/мусором,
   срабатывает локальный движок, который реально анализирует оценки, ключевые слова и тексты
   отзывов товара. Ни одна публичная функция модуля не выбрасывает исключений наружу из-за AI.
"""
import difflib
import json
import logging
import re
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from django.conf import settings
from django.db.models import Avg, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

MIN_TIMEOUT, MAX_TIMEOUT = 5.0, 8.0

# --------------------------------------------------------------------------------------
# Раскладка клавиатуры и стоп-слова
# --------------------------------------------------------------------------------------
EN_TO_RU = str.maketrans("qwertyuiop[]asdfghjkl;'zxcvbnm,.", "йцукенгшщзхъфывапролджэячсмитьбю")
RU_TO_EN = str.maketrans("йцукенгшщзхъфывапролджэячсмитьбю", "qwertyuiop[]asdfghjkl;'zxcvbnm,.")

FILLER_WORDS = {
    "товары", "товар", "твоары", "тавары", "все", "всё", "гаджеты", "гаджет",
    "девайсы", "девайс", "устройства", "техника", "покажи", "найди", "подбери", "ищу",
    "нужен", "нужна", "нужно", "хочу", "купить", "подарить", "выбрать", "для", "что", "как",
    "или", "под", "мне", "до", "от", "бюджет", "дешевле", "дороже", "максимум", "минимум",
    "за", "по", "цене", "цена", "стоимостью", "долларов", "доллара", "долл", "баксов", "usd",
    "рублей", "штуки", "пожалуйста", "порекомендуй", "посоветуй", "подскажи", "какой", "какая",
    "лучший", "лучшая", "хороший", "хорошая", "недорогой", "недорогая",
}
_FILLER_LIST = sorted(FILLER_WORDS)
_TYPO_FILLER_RE = re.compile(r"^т[оав]{2,3}р")


def cyr_cases(word):
    """Варианты регистра: LIKE в SQLite регистронезависим только для ASCII, поэтому для
    кириллицы перебираем регистры явно (на PostgreSQL ``icontains`` работает и так)."""
    return {word.lower(), word.capitalize(), word.upper(), word.title()}


def _is_filler(word):
    if word in FILLER_WORDS or _TYPO_FILLER_RE.match(word):
        return True
    if len(word) >= 5:  # опечатки стоп-слов: «покажы», «подбири», «хачу»
        return bool(difflib.get_close_matches(word, _FILLER_LIST, n=1, cutoff=0.84))
    return False


# --------------------------------------------------------------------------------------
# Конфигурация и HTTP-клиент для внешних LLM
# --------------------------------------------------------------------------------------
def _conf(name, default=""):
    """Значение из django.conf.settings (источник истины); env — только если атрибута нет."""
    if hasattr(settings, name):
        value = getattr(settings, name)
        return default if value is None else value
    import os
    return os.getenv(name, default)


def _timeout():
    try:
        value = float(_conf("AI_HTTP_TIMEOUT", "6"))
    except (TypeError, ValueError):
        value = 6.0
    return max(MIN_TIMEOUT, min(MAX_TIMEOUT, value))


def _providers():
    """Список настроенных провайдеров в порядке приоритета."""
    providers = []
    if str(_conf("OPENAI_API_KEY")).strip():
        providers.append({
            "name": "openai", "kind": "openai",
            "key": str(_conf("OPENAI_API_KEY")).strip(),
            "base_url": str(_conf("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/"),
            "model": str(_conf("OPENAI_MODEL", "gpt-4o-mini")).strip(),
        })
    if str(_conf("GEMINI_API_KEY")).strip():
        providers.append({
            "name": "gemini", "kind": "gemini",
            "key": str(_conf("GEMINI_API_KEY")).strip(),
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "model": str(_conf("GEMINI_MODEL", "gemini-2.0-flash")).strip(),
        })
    if str(_conf("AI_API_KEY")).strip():  # легаси: любой OpenAI-совместимый провайдер
        providers.append({
            "name": "compat", "kind": "openai",
            "key": str(_conf("AI_API_KEY")).strip(),
            "base_url": str(_conf("AI_BASE_URL", "https://api.deepseek.com/v1")).rstrip("/"),
            "model": str(_conf("AI_MODEL", "deepseek-chat")).strip(),
        })
    return providers


def llm_enabled():
    return bool(_providers())


def configured_providers():
    """Имена настроенных LLM-провайдеров (без ключей) — для healthcheck."""
    return [p["name"] for p in _providers()]


def _http_post_json(url, payload, headers, timeout):
    """POST JSON с жёстким таймаутом. Возвращает dict либо бросает исключение."""
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("Недопустимая схема URL для LLM API")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "SokolykOff Shop/2026", **headers},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (схема проверена выше)
        return json.loads(response.read().decode("utf-8"))


def _call_openai_compatible(provider, prompt, system, max_tokens, timeout):
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    data = _http_post_json(
        f"{provider['base_url']}/chat/completions",
        {"model": provider["model"], "messages": messages, "max_tokens": max_tokens, "temperature": 0.3},
        {"Authorization": f"Bearer {provider['key']}"},
        timeout,
    )
    return data["choices"][0]["message"]["content"]


def _call_gemini(provider, prompt, system, max_tokens, timeout):
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    data = _http_post_json(
        f"{provider['base_url']}/models/{provider['model']}:generateContent",
        payload,
        {"x-goog-api-key": provider["key"]},  # ключ в заголовке, а не в URL — не попадёт в логи
        timeout,
    )
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(part.get("text", "") for part in parts)


def call_llm(prompt, system=None, max_tokens=500):
    """Вызывает первый доступный LLM. Возвращает ``(text, provider_name)`` или ``None``.

    Никогда не бросает исключений: таймаут, сетевая ошибка, HTTP 4xx/5xx, битый JSON и пустой
    ответ приводят к переходу на следующий провайдер, а затем — к локальному движку."""
    timeout = _timeout()
    for provider in _providers():
        try:
            if provider["kind"] == "gemini":
                text = _call_gemini(provider, prompt, system, max_tokens, timeout)
            else:
                text = _call_openai_compatible(provider, prompt, system, max_tokens, timeout)
            text = (text or "").strip()
            if text:
                return text, provider["name"]
            logger.warning("LLM %s вернул пустой ответ", provider["name"])
        except urllib.error.HTTPError as exc:
            logger.warning("LLM %s: HTTP %s", provider["name"], exc.code)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("LLM %s: сетевая ошибка (%s)", provider["name"], type(exc).__name__)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("LLM %s: некорректный ответ (%s)", provider["name"], type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 — AI не должен ронять магазин ни при каких условиях
            logger.exception("LLM %s: непредвиденная ошибка %s", provider["name"], type(exc).__name__)
    return None


# --------------------------------------------------------------------------------------
# Парсер бюджета
# --------------------------------------------------------------------------------------
_NUM = r"(\d{1,3}(?:[ \u00a0]\d{3})+|\d+(?:[.,]\d+)?)\s*(?:(к|k|тыс\w*)(?![а-яa-z]))?"
_CUR = r"\s*(?:\$|usd|долл\w*|бакс\w*)?"
_RANGE_RE = re.compile(rf"(?:от|from)\s*\$?\s*{_NUM}{_CUR}\s*(?:до|to|-|–|—)\s*\$?\s*{_NUM}")
_MAX_RE = re.compile(rf"(?:не\s+дороже|не\s+больше|до|дешевле|бюджет|максимум|менее|меньше|under|<=|<)\s*\$?\s*{_NUM}")
_MIN_RE = re.compile(rf"(?:дороже|минимум|свыше|больше|более|от|>=|>)\s*\$?\s*{_NUM}")


def _to_decimal(number, suffix=None):
    try:
        value = Decimal(number.replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None
    if suffix:
        value *= 1000
    return value


def parse_price_bounds(text):
    """«игровой пк до 1500$» -> (None, Decimal(1500), 'игровой пк').

    Понимает «до X», «от X», «от X до Y», «не дороже X», «до 1 500», «до 1.5к», знак ``$``."""
    q = (text or "").lower()
    min_price = max_price = None

    range_m = _RANGE_RE.search(q)
    if range_m:
        a, b = _to_decimal(range_m.group(1), range_m.group(2)), _to_decimal(range_m.group(3), range_m.group(4))
        if a is not None and b is not None:
            min_price, max_price = (a, b) if a <= b else (b, a)
            q = q[:range_m.start()] + " " + q[range_m.end():]
    else:
        max_m = _MAX_RE.search(q)
        if max_m:
            max_price = _to_decimal(max_m.group(1), max_m.group(2))
            q = q[:max_m.start()] + " " + q[max_m.end():]
        min_m = _MIN_RE.search(q)
        if min_m:
            min_price = _to_decimal(min_m.group(1), min_m.group(2))
            q = q[:min_m.start()] + " " + q[min_m.end():]
        if min_price is not None and max_price is not None and min_price > max_price:
            min_price, max_price = max_price, min_price

    words = [w for w in re.findall(r"[a-zа-яё0-9]+", q) if not _is_filler(w)]
    if words and all(w.isdigit() for w in words):  # голые числа без слов — это не поисковая фраза
        words = []
    return min_price, max_price, " ".join(words).strip()


# --------------------------------------------------------------------------------------
# Гибридный умный поиск
# --------------------------------------------------------------------------------------
def _stem(word):
    return word[:4] if len(word) > 5 else word


def _tokens(text):
    return [w for w in text.split() if (len(w) >= 3 or (w.isdigit() and len(w) >= 2)) and not _is_filler(w)]


def _field_q(fields, stem):
    q = Q()
    for case in cyr_cases(stem):
        for field in fields:
            q |= Q(**{f"{field}__icontains": case})
    return q


def _rank_stage(queryset, variants, fields):
    """Двухступенчатое ранжирование внутри одной группы полей: сперва совпадение ВСЕХ слов
    (хотя бы в одном варианте раскладки), затем — ЛЮБОГО из слов."""
    token_sets = [[_stem(w) for w in _tokens(v)] for v in variants]
    token_sets = [ts for ts in token_sets if ts]
    if not token_sets:
        return None

    all_q = Q()
    for ts in token_sets:
        group = Q()
        for stem in ts:
            group &= _field_q(fields, stem)
        all_q |= group
    strict = queryset.filter(all_q).distinct()
    if strict.exists():
        return strict

    any_q = Q()
    for ts in token_sets:
        for stem in ts:
            any_q |= _field_q(fields, stem)
    loose = queryset.filter(any_q).distinct()
    return loose if loose.exists() else None


def _vocabulary(queryset):
    model = queryset.model
    vocab = set()
    rows = model.objects.values_list("title", "search_keywords", "category__title")[:2000]
    for row in rows:
        for chunk in row:
            for w in re.findall(r"[a-zа-яё]{4,}", (chunk or "").lower()):
                vocab.add(w)
    return vocab


def _correct_typos(words, vocab):
    """Исправление опечаток по словарю каталога («клыва» -> «клавиатура»)."""
    corrected = []
    for w in words:
        if len(w) < 4 or w.isdigit():
            continue
        best, best_score = None, 0.0
        for v in vocab:
            if v[0] != w[0] and v[1:2] != w[1:2]:
                continue
            score = max(difflib.SequenceMatcher(None, w, v[: len(w)]).ratio(),
                        difflib.SequenceMatcher(None, w, v[: len(w) + 1]).ratio())
            if score > best_score:
                best, best_score = v, score
        if best and best != w and (best_score >= 0.74 or (len(w) >= 5 and best_score >= 0.55 and best[:2] == w[:2])):
            corrected.append(best)
    return corrected


def smart_search_products(queryset, raw_query, budget_fallback=True):
    """Гибридный поиск: бюджет -> название -> категория/описание/теги -> опечатки -> бюджет."""
    raw = (raw_query or "").strip()
    if not raw:
        return queryset

    min_p, max_p, clean_text = parse_price_bounds(raw)
    if min_p is not None:
        queryset = queryset.filter(price__gte=min_p)
    if max_p is not None:
        queryset = queryset.filter(price__lte=max_p)

    if not clean_text:
        return queryset.order_by("price")

    variants = []
    for v in (clean_text, clean_text.translate(EN_TO_RU), clean_text.translate(RU_TO_EN)):
        if v and v not in variants:
            variants.append(v)

    title_fields = ["title"]
    deep_fields = ["category__title", "description", "search_keywords", "tags__name"]

    # ПРИОРИТЕТ 1: совпадение в названии товара (аксессуары не должны вытеснять устройство)
    for fields in (title_fields, deep_fields):
        found = _rank_stage(queryset, variants, fields)
        if found is not None:
            return found

    # ПРИОРИТЕТ 2: опечатки — исправляем слова по словарю каталога и повторяем поиск
    fixed = _correct_typos([w for v in variants[:1] for w in _tokens(v)], _vocabulary(queryset))
    if fixed:
        for fields in (title_fields, deep_fields):
            found = _rank_stage(queryset, [" ".join(fixed)], fields)
            if found is not None:
                return found

    # ПРИОРИТЕТ 3: слова не распознаны, но бюджет задан — показываем всё в бюджете
    if budget_fallback and (min_p is not None or max_p is not None):
        return queryset.order_by("price")
    return queryset.none()


# --------------------------------------------------------------------------------------
# AI-саммаризатор отзывов: локальный движок
# --------------------------------------------------------------------------------------
ASPECTS = {
    "производительность": (
        ("тянет", "мощн", "быстр", "производительн", "летает", "шустр", "fps", "плавн", "справляется"),
        ("тормоз", "медлен", "лагает", "слаб", "фриз", "просад"),
    ),
    "охлаждение и шум": (
        ("холодн", "тих", "бесшумн", "шумит", "греет", "температур", "пищ"),
        ("гудит", "жужж", "перегрев", "горяч", "громк", "шумн"),
    ),
    "качество сборки": (
        ("качествен", "сборк", "надежн", "надёжн", "прочн", "металл", "добротн", "материал"),
        ("люфт", "скрип", "хлипк", "треснул", "сломал", "брак", "дешев"),
    ),
    "цена": (
        ("недорог", "выгодн", "своих денег", "доступн", "оправдан"),
        ("дорог", "переплат", "кусается", "завышен"),
    ),
    "экран и картинка": (
        ("экран", "картинк", "яркий", "яркост", "цвет", "чётк", "четк", "oled", "изображени"),
        ("блик", "тускл", "мерцан", "пиксел", "засвет"),
    ),
    "звук": (
        ("звук", "басы", "шумоподавлен", "микрофон"),
        ("хрип", "шипит", "тихий звук", "эхо"),
    ),
    "автономность": (
        ("автономн", "держит", "работает до", "заряд"),
        ("садится", "разряжа"),
    ),
    "удобство": (
        ("удобн", "эргоном", "комфортн", "легк", "лёгк", "компактн", "интуитивн"),
        ("неудобн", "тяжел", "тяжёл", "жестк", "громозд"),
    ),
    "софт и совместимость": (
        ("драйвер", "совместим", "подключил", "настроил", "работает из коробки", "без проблем"),
        ("глюч", "вылет", "баг", "не определя", "несовместим", "драйвера слет"),
    ),
}
_NEG_CUES = {"тормоз", "медлен", "лагает", "слаб", "фриз", "просад", "гудит", "жужж", "перегрев", "горяч",
             "громк", "шумн", "люфт", "скрип", "хлипк", "треснул", "сломал", "брак", "дешев", "дорог",
             "переплат", "кусается", "завышен", "блик", "тускл", "мерцан", "пиксел", "засвет", "хрип",
             "шипит", "эхо", "садится", "разряжа", "неудобн", "тяжел", "тяжёл", "жестк", "громозд",
             "глюч", "вылет", "баг", "несовместим"}
# Эти «позитивные» корни при отрицании («не греется», «не шумит») меняют знак на положительный
_NEGATABLE = re.compile(r"\bне\s+(?:\w+\s+){0,1}$")
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")


def _sentences(text):
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if len(s.strip()) >= 8]


def _short(text, limit=110):
    text = re.sub(r"\s+", " ", text).strip().rstrip(".!…")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _plural(n, forms):
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return forms[2]
    return forms[0] if n % 10 == 1 else forms[1] if 2 <= n % 10 <= 4 else forms[2]


def _sentence_polarity(sentence, aspect):
    """+1 / -1 / 0 по ключевым словам аспекта с учётом отрицания («не греется» = плюс)."""
    s = sentence.lower()
    pos_cues, neg_cues = ASPECTS[aspect]
    score = 0
    for cue in pos_cues + neg_cues:
        idx = s.find(cue)
        if idx < 0:
            continue
        is_neg = cue in _NEG_CUES or cue in neg_cues
        negated = bool(_NEGATABLE.search(s[:idx]))
        value = -1 if is_neg else 1
        score += -value if negated else value
    return (score > 0) - (score < 0)


def analyze_reviews_locally(product, reviews):
    """Smart Fallback: реальный анализ звёзд, ключевых слов и текстов отзывов товара."""
    n = len(reviews)
    avg = sum(r.rate for r in reviews) / n
    positive_share = round(100 * sum(1 for r in reviews if r.rate >= 4) / n)

    aspect_pos, aspect_neg = {}, {}
    neg_sentences = []
    for review in reviews:
        for sentence in _sentences(review.text):
            for aspect in ASPECTS:
                pol = _sentence_polarity(sentence, aspect)
                if pol > 0 and review.rate >= 3:
                    aspect_pos.setdefault(aspect, []).append((review.rate, sentence))
                elif pol < 0:
                    aspect_neg.setdefault(aspect, []).append((review.rate, sentence))
            if review.rate <= 2 and sentence not in neg_sentences:
                neg_sentences.append(sentence)

    pros, cons = [], []
    used = set()  # одна и та же фраза не должна дублироваться в нескольких аспектах
    for aspect, hits in sorted(aspect_pos.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if aspect in aspect_neg and len(aspect_neg[aspect]) >= len(hits):
            continue
        best = max(hits, key=lambda h: (h[0], -len(h[1])))[1]
        if best in used:
            continue
        used.add(best)
        pros.append(f"{aspect.capitalize()}: отмечают {len(hits)} из {n} — «{_short(best, 90)}»")
    for aspect, hits in sorted(aspect_neg.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        worst = min(hits, key=lambda h: (h[0], len(h[1])))[1]
        if worst in used:
            continue
        used.add(worst)
        cons.append(f"{aspect.capitalize()}: замечание в {len(hits)} из {n} — «{_short(worst, 90)}»")
    for sentence in neg_sentences:
        if len(cons) >= 3:
            break
        if not any(sentence[:25] in c for c in cons):
            cons.append(f"Критика покупателей: «{_short(sentence, 100)}»")

    if not pros:
        top = max(reviews, key=lambda r: (r.rate, -len(r.text)))
        first = (_sentences(top.text) or [top.text])[0]
        pros.append(f"Лучший отзыв ({top.rate}★): «{_short(first, 100)}»")

    service_specs = ("категория", "статус склада", "гарантия")
    specs = [s for s in product.specifications.all() if s.name.lower() not in service_specs][:3]
    if specs and len(pros) < 3:
        pros.append("Характеристики: " + ", ".join(f"{s.name} — {s.value}" for s in specs))

    # Цена и остатки — объективные факторы для блока «Минусы / Ограничения»
    peers_avg = (type(product).objects.filter(category_id=product.category_id)
                 .exclude(pk=product.pk).aggregate(a=Avg("price"))["a"])
    if peers_avg:
        ratio = float(product.price) / float(peers_avg)
        if ratio >= 1.3:
            cons.append(f"Цена ${product.price:.0f} выше средней по категории (${float(peers_avg):.0f}) — премиум-сегмент")
        elif ratio <= 0.75:
            pros.append(f"Цена ${product.price:.0f} заметно ниже средней по категории (${float(peers_avg):.0f})")
    if product.count == 0:
        cons.append("Сейчас нет в наличии")
    elif product.count <= 5:
        cons.append(f"Ограниченный остаток на складе: {product.count} шт.")
    if not cons:
        cons.append("Существенных недостатков покупатели не отметили")

    if avg >= 4.5:
        verdict = "Однозначно рекомендуем: покупатели почти единодушны в положительной оценке."
    elif avg >= 4.0:
        verdict = "Рекомендуем: сильный выбор, но учтите отмеченные ограничения."
    elif avg >= 3.0:
        verdict = "Подойдёт при условии, что отмеченные недостатки для вас не критичны."
    else:
        verdict = "Не рекомендуем без сравнения: оценки покупателей заметно ниже среднего."
    verdict = (f"{verdict} Средний балл {avg:.1f} из 5 по {n} {_plural(n, ('отзыву', 'отзывам', 'отзывам'))}, "
               f"{positive_share}% оценок — 4–5 звёзд.")
    if n < 3:
        verdict += " Выборка пока небольшая, оценка предварительная."

    return {"pros": pros[:4], "cons": cons[:4], "verdict": verdict, "avg": round(avg, 1),
            "count": n, "source": "local"}


def _llm_summary(product, reviews):
    """Саммаризация через внешний LLM. Возвращает dict или None (тогда сработает fallback)."""
    if not llm_enabled():
        return None
    avg = sum(r.rate for r in reviews) / len(reviews)
    review_lines = "\n".join(f"- [{r.rate}★] {r.text[:300]}" for r in reviews[:25])
    specs = "; ".join(f"{s.name}: {s.value}" for s in product.specifications.all()[:10])
    prompt = (
        f"Товар: {product.title}\nЦена: ${product.price}\nХарактеристики: {specs or 'нет данных'}\n"
        f"Средняя оценка: {avg:.1f}/5 ({len(reviews)} отзывов)\nОтзывы:\n{review_lines}\n\n"
        'Верни СТРОГО JSON без пояснений: {"pros": ["..."], "cons": ["..."], "verdict": "..."}. '
        "pros и cons — по 2-4 коротких пункта на русском, только на основе отзывов, характеристик и цены. "
        "verdict — 1-2 предложения с итоговой рекомендацией."
    )
    result = call_llm(prompt, system="Ты аналитик отзывов интернет-магазина компьютерного железа. Отвечай только JSON.",
                      max_tokens=600)
    if not result:
        return None
    text, provider = result
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else None
        pros = [str(x).strip() for x in data["pros"] if str(x).strip()][:4]
        cons = [str(x).strip() for x in data["cons"] if str(x).strip()][:4]
        verdict = str(data["verdict"]).strip()
    except (TypeError, KeyError, ValueError, AttributeError):
        logger.warning("LLM %s вернул JSON неожиданной структуры — включаем fallback", provider)
        return None
    if not pros or not cons or not verdict:
        return None
    return {"pros": pros, "cons": cons, "verdict": verdict, "avg": round(avg, 1),
            "count": len(reviews), "source": provider}


def generate_review_summary(product, reviews):
    """LLM (если настроен) -> локальный движок. Всегда возвращает валидную сводку."""
    try:
        summary = _llm_summary(product, reviews)
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка LLM-саммаризации, используем fallback")
        summary = None
    return summary or analyze_reviews_locally(product, reviews)


def _load_cached(raw):
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("pros") and data.get("cons") and data.get("verdict"):
            return data
    except (TypeError, ValueError):
        pass
    return None


def update_product_ai_summary(product, force_refresh=False):
    """Возвращает структурированную сводку отзывов и кэширует её в ``Product.ai_summary``.

    * чтение: сводка отдаётся мгновенно из БД (``cached=True``) без обращения к LLM;
    * ``force_refresh=True`` (новый отзыв) — безусловный пересчёт;
    * защитная проверка: если число отзывов не совпадает с числом в кэше (отзыв добавили через
      админку/bulk, минуя вью) — кэш считается устаревшим и пересчитывается автоматически."""
    reviews = list(product.reviews.all())
    if not reviews:
        return {"empty": True, "pros": [], "cons": [], "verdict": "", "avg": 0, "count": 0,
                "source": "none", "cached": False, "updated_at": None}

    cached = None if force_refresh else _load_cached(product.ai_summary)
    if cached is not None and cached.get("count") == len(reviews):
        updated = product.ai_updated_at.isoformat() if product.ai_updated_at else None
        return {**cached, "empty": False, "cached": True, "updated_at": updated}

    summary = generate_review_summary(product, reviews)
    product.ai_summary = json.dumps(summary, ensure_ascii=False)
    product.ai_updated_at = timezone.now()
    product.save(update_fields=["ai_summary", "ai_updated_at"])
    return {**summary, "empty": False, "cached": False, "updated_at": product.ai_updated_at.isoformat()}


def invalidate_product_ai_summary(product_id):
    """Дешёвая инвалидация кэша (без вызова LLM) — для сигналов Review.post_save/post_delete."""
    from .models import Product
    Product.all_objects.filter(pk=product_id).update(ai_summary="", ai_updated_at=None)


# --------------------------------------------------------------------------------------
# AI-консультант (/api/ai-assistant/)
# --------------------------------------------------------------------------------------
def _product_payload(product):
    image = product.images.all()[0].image if product.images.all() else None
    try:
        image_url = image.url if image else ""
    except ValueError:
        image_url = ""
    return {"id": product.id, "title": product.title, "price": float(product.price),
            "count": product.count, "url": f"/product/{product.id}", "image": image_url}


def _local_reply(query, matched, total, min_p, max_p, alternatives):
    budget = ""
    if min_p is not None and max_p is not None:
        budget = f" в бюджете ${min_p:.0f}–${max_p:.0f}"
    elif max_p is not None:
        budget = f" в бюджете до ${max_p:.0f}"
    elif min_p is not None:
        budget = f" дороже ${min_p:.0f}"

    if matched:
        best = matched[0]
        text = (f"Нашёл {total} {_plural(total, ('товар', 'товара', 'товаров'))} в наличии{budget}. "
                f"Лучший выбор — {best.title} за ${best.price:.0f}")
        if best.description:
            text += f": {_short(best.description, 120)}"
        else:
            text += "."
        if len(matched) > 1:
            text += f" Также стоит взглянуть на {matched[1].title} (${matched[1].price:.0f})."
        return text
    if alternatives:
        alt = alternatives[0]
        return (f"В наличии ничего не нашлось{budget}. Ближайший вариант — {alt.title} за ${alt.price:.0f}: "
                "возможно, стоит чуть увеличить бюджет.")
    return "По такому запросу в наличии ничего не нашлось. Попробуйте другое название или увеличьте бюджет."


def _llm_reply(query, matched, min_p, max_p):
    if not matched or not llm_enabled():
        return None
    catalog = "\n".join(f"- {p.title} — ${p.price:.0f}, в наличии {p.count} шт. {_short(p.description, 140)}"
                        for p in matched)
    budget = f"Бюджет клиента: до ${max_p:.0f}. " if max_p is not None else ""
    prompt = (f"Запрос клиента: «{query}». {budget}\nПодходящие товары, которые есть в наличии:\n{catalog}\n\n"
              "Ответь клиенту по-русски в 2-4 предложениях: порекомендуй 1-2 товара из списка и коротко объясни выбор. "
              "Упоминай только товары из списка, не выдумывай характеристики и цены. Без markdown.")
    result = call_llm(prompt, system="Ты консультант магазина компьютерного железа SokolykOff Shop.", max_tokens=300)
    if not result:
        return None
    return result[0].replace("*", "").strip(), result[1]


def ask_shopping_assistant(query_text, products_qs):
    """Подбор ТОЛЬКО из товаров в наличии (count > 0) под бюджет пользователя."""
    q = (query_text or "").strip()
    if not q:
        return {"reply": "Привет! Я ИИ-ассистент SokolykOff Shop. Напишите, что ищете и ваш бюджет "
                         "(например: «игровой ноутбук до 1700» или «тихая клавиатура до 200$»).",
                "items": [], "source": "local"}

    min_p, max_p, _ = parse_price_bounds(q)
    in_stock = products_qs.filter(count__gt=0).select_related("category").prefetch_related("images")
    found = smart_search_products(in_stock, q, budget_fallback=False)
    matched = list(found.order_by("-purchases_count", "-price")[:5])
    total = found.count()

    alternatives = []
    if not matched and max_p is not None:
        _, _, topic = parse_price_bounds(q)
        pricier = in_stock.filter(price__gt=max_p)
        pool = smart_search_products(pricier, topic, budget_fallback=False) if topic else pricier
        alternatives = list(pool.order_by("price")[:1])

    reply, source = None, "local"
    llm = _llm_reply(q, matched, min_p, max_p)
    if llm:
        reply, source = llm
    if not reply:
        reply = _local_reply(q, matched, total, min_p, max_p, alternatives)

    return {"reply": reply, "items": [_product_payload(p) for p in (matched or alternatives)], "source": source}
