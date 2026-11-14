"""OpenAPI 3 схема и Swagger UI без сторонних зависимостей.

Эндпоинты магазина написаны на чистых Django-вью (JsonResponse), поэтому авто-генераторы
вроде drf-spectacular схему по ним не построят. Схема описана декларативно и покрывается
тестом, который сверяет её с реальным urlconf (документация не может «разъехаться» с кодом)."""
from django.http import HttpResponse, JsonResponse

PRODUCT_ID = {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
ERR = {"description": "Ошибка", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}


def _op(summary, tag, *, params=None, body=None, ok="Успешный ответ", schema=None, codes=()):
    op = {"summary": summary, "tags": [tag], "responses": {
        "200": {"description": ok, "content": {"application/json": {"schema": schema or {"type": "object"}}}}}}
    if params:
        op["parameters"] = params
    if body:
        op["requestBody"] = {"required": True, "content": {"application/json": {"schema": body}}}
    for code in codes:
        op["responses"][str(code)] = ERR
    return op


def _ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def _arr(name):
    return {"type": "array", "items": _ref(name)}


def build_schema():
    q = lambda name, desc, typ="string": {"name": name, "in": "query", "description": desc, "schema": {"type": typ}}  # noqa: E731
    paths = {
        "/api/health/": {"get": _op("Healthcheck: БД, остатки, режим AI-движка", "Система")},
        "/api/ai-assistant/": {
            "post": _op("AI-консультант: подбор товаров в наличии под бюджет", "AI",
                        body={"type": "object", "properties": {"message": {"type": "string", "example": "тихая клавиатура до 200$"}}},
                        schema=_ref("AssistantReply")),
            "get": _op("AI-консультант (GET ?q=)", "AI", params=[q("q", "Запрос на естественном языке")],
                       schema=_ref("AssistantReply")),
        },
        "/api/categories/": {"get": _op("Дерево категорий", "Каталог")},
        "/api/banners/": {"get": _op("Товары для баннеров главной", "Каталог", schema=_arr("ProductShort"))},
        "/api/products/popular/": {"get": _op("Популярные товары", "Каталог", schema=_arr("ProductShort"))},
        "/api/products/limited/": {"get": _op("Лимитированные серии", "Каталог", schema=_arr("ProductShort"))},
        "/api/catalog/": {"get": _op(
            "Каталог: умный поиск, фильтры, сортировка, пагинация", "Каталог",
            params=[q("filter", "Поисковая фраза («ноутбук до 700», «rkfdf», «от 200 до 600»)"),
                    q("filter[minPrice]", "Минимальная цена", "number"), q("filter[maxPrice]", "Максимальная цена", "number"),
                    q("filter[freeDelivery]", "true — только с бесплатной доставкой"),
                    q("filter[available]", "true — только в наличии"), q("category", "ID категории", "integer"),
                    q("sort", "price | reviews | rating | date"), q("sortType", "inc | dec"),
                    q("currentPage", "Страница", "integer"), q("limit", "Размер страницы (1–100)", "integer")])},
        "/api/product/{id}/": {"get": _op("Карточка товара + структурированная AI-сводка отзывов (aiSummary)", "Каталог",
                                          params=[PRODUCT_ID], schema=_ref("ProductFull"), codes=(404,))},
        "/api/product/{id}/reviews/": {
            "get": _op("Отзывы товара", "Каталог", params=[PRODUCT_ID], codes=(404,)),
            "post": _op("Добавить отзыв (только авторизованные). Пересчитывает AI-сводку", "Каталог", params=[PRODUCT_ID],
                        body={"type": "object", "properties": {"text": {"type": "string"}, "rate": {"type": "integer", "minimum": 1, "maximum": 5}}},
                        codes=(403, 404)),
        },
        "/api/sales/": {"get": _op("Акции и скидки", "Каталог", params=[q("currentPage", "Страница", "integer")])},
        "/api/tags/": {"get": _op("Теги", "Каталог")},
        "/api/basket/": {
            "get": _op("Корзина (сессионная)", "Заказы"),
            "post": _op("Добавить товар в корзину", "Заказы", body={"type": "object", "properties": {"id": {"type": "integer"}, "count": {"type": "integer"}}}),
            "delete": _op("Убрать товар из корзины", "Заказы"),
        },
        "/api/orders/": {
            "get": _op("Мои заказы", "Заказы"),
            "post": _op("Оформить заказ: атомарное резервирование остатков (select_for_update). 409 — товара не хватает",
                        "Заказы", codes=(400, 409)),
        },
        "/api/order/{id}/": {
            "get": _op("Заказ (только владелец)", "Заказы", params=[PRODUCT_ID], codes=(404,)),
            "post": _op("Подтвердить заказ: доставка и контакты", "Заказы", params=[PRODUCT_ID], codes=(404, 409)),
        },
        "/api/payment/{id}/": {"post": _op("Оплата заказа (Celery-задача, идемпотентна)", "Заказы", params=[PRODUCT_ID],
                                           body={"type": "object", "properties": {"number": {"type": "string"}, "month": {"type": "string"}, "code": {"type": "string"}}},
                                           codes=(400, 404))},
        "/api/account/": {"get": _op("Аккаунт и последние заказы", "Пользователь", codes=(401,))},
        "/api/sign-in/": {"post": _op("Вход", "Пользователь", codes=(400,))},
        "/api/sign-up/": {"post": _op("Регистрация", "Пользователь", codes=(400,))},
        "/api/sign-out/": {"post": _op("Выход", "Пользователь")},
        "/api/profile/": {"get": _op("Профиль", "Пользователь", codes=(401,)), "post": _op("Обновить профиль", "Пользователь")},
        "/api/profile/avatar/": {"post": _op("Загрузить аватар (до 2 МБ)", "Пользователь")},
        "/api/profile/password/": {"post": _op("Сменить пароль", "Пользователь", codes=(400,))},
        "/api/profile/delete/": {"post": _op("Удалить аккаунт (деактивация)", "Пользователь", codes=(401,))},
    }
    schemas = {
        "Error": {"type": "object", "properties": {"error": {"type": "string"}}},
        "ProductShort": {"type": "object", "properties": {
            "id": {"type": "integer"}, "title": {"type": "string"}, "price": {"type": "number"}, "count": {"type": "integer"},
            "rating": {"type": "number"}, "reviews": {"type": "integer"}, "images": {"type": "array", "items": {"type": "object"}}}},
        "AiSummary": {"type": "object", "description": "Кэшируется в Product.ai_summary; пересчитывается при новом отзыве", "properties": {
            "pros": {"type": "array", "items": {"type": "string"}}, "cons": {"type": "array", "items": {"type": "string"}},
            "verdict": {"type": "string"}, "avg": {"type": "number"}, "count": {"type": "integer"},
            "source": {"type": "string", "description": "local | openai | gemini | compat"},
            "cached": {"type": "boolean"}, "empty": {"type": "boolean"}}},
        "ProductFull": {"allOf": [_ref("ProductShort"), {"type": "object", "properties": {
            "fullDescription": {"type": "string"}, "aiSummary": _ref("AiSummary"),
            "specifications": {"type": "array", "items": {"type": "object"}}}}]},
        "AssistantReply": {"type": "object", "properties": {
            "reply": {"type": "string"}, "source": {"type": "string"}, "items": {"type": "array", "items": {"type": "object"}}}},
    }
    return {
        "openapi": "3.0.3",
        "info": {"title": "SokolykOff Shop API", "version": "2026.11",
                 "description": "AI-магазин компьютерного железа: умный поиск, AI-аналитика отзывов, консультант, заказы."},
        "tags": [{"name": t} for t in ("AI", "Каталог", "Заказы", "Пользователь", "Система")],
        "paths": paths,
        "components": {"schemas": schemas},
    }


def schema_view(request):
    return JsonResponse(build_schema(), json_dumps_params={"ensure_ascii": False})


SWAGGER_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>SokolykOff Shop API — документация</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"></head>
<body><div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>window.ui = SwaggerUIBundle({url: "/api/schema/", dom_id: "#swagger-ui", deepLinking: true});</script>
</body></html>"""


def docs_view(request):
    return HttpResponse(SWAGGER_HTML)
