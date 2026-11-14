from decimal import Decimal, InvalidOperation

from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Avg, Count, Prefetch, Q
from django.http import JsonResponse

from users.views import parse_body, serialize_image
from .ai_service import (ask_shopping_assistant, configured_providers, llm_enabled, smart_search_products,
                         update_product_ai_summary)
from .models import Category, Product, Review, Sale, Tag

PRODUCT_PREFETCH = ("images", "tags", "reviews")


def _int(value, default, minimum=1, maximum=100):
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _not_found(message="Товар не найден"):
    return JsonResponse({"error": message}, status=404)


def serialize_product_short(product, count_override=None):
    """Сериализация без лишних запросов: images/tags/reviews берутся из prefetch-кэша,
    рейтинг считается в памяти по уже загруженным отзывам (никакого N+1)."""
    images = [serialize_image(img.image, img.alt or product.title) for img in product.images.all()]
    if not images:
        images = [serialize_image(None, product.title)]
    reviews = list(product.reviews.all())
    avg_rating = sum(r.rate for r in reviews) / len(reviews) if reviews else 0
    return {
        "id": product.id,
        "category": product.category_id,
        "price": float(product.price),
        "count": count_override if count_override is not None else product.count,
        "date": product.date.strftime("%a %b %d %Y %H:%M:%S GMT+0300"),
        "title": product.title,
        "description": product.description,
        "shortDescription": product.description,
        "freeDelivery": product.free_delivery,
        "images": images,
        "tags": [{"id": t.id, "name": t.name} for t in product.tags.all()],
        "reviews": len(reviews),
        "rating": round(float(avg_rating), 1),
    }


def serialize_product_full(product):
    data = serialize_product_short(product)
    # fullDescription — чистое описание; AI-аналитика отдаётся структурно в поле aiSummary
    data["fullDescription"] = product.full_description or product.description
    data["aiSummary"] = update_product_ai_summary(product)
    data["specifications"] = [{"name": s.name, "value": s.value} for s in product.specifications.all()]
    data["reviews"] = serialize_reviews(product)
    return data


def serialize_reviews(product):
    return [
        {"author": r.author, "text": r.text, "rate": r.rate,
         "date": r.date.strftime("%Y-%m-%d %H:%M")}
        for r in product.reviews.all()
    ]


def products_qs():
    return Product.objects.prefetch_related(*PRODUCT_PREFETCH)


def categories_view(request):
    subs = Prefetch("subcategories", queryset=Category.objects.all(), to_attr="active_subs")
    roots = Category.objects.filter(parent__isnull=True).prefetch_related(subs)
    return JsonResponse([
        {
            "id": cat.id,
            "title": cat.title,
            "image": serialize_image(cat.image, cat.title),
            "subcategories": [
                {"id": sub.id, "title": sub.title, "image": serialize_image(sub.image, sub.title)}
                for sub in cat.active_subs
            ],
        }
        for cat in roots
    ], safe=False)


def banners_view(request):
    return JsonResponse([serialize_product_short(p) for p in products_qs()[:3]], safe=False)


def popular_products_view(request):
    products = products_qs().order_by("sort_index", "-purchases_count")[:8]
    return JsonResponse([serialize_product_short(p) for p in products], safe=False)


def limited_products_view(request):
    products = products_qs().filter(limited_edition=True)[:16]
    return JsonResponse([serialize_product_short(p) for p in products], safe=False)


def tags_view(request):
    return JsonResponse([{"id": t.id, "name": t.name} for t in Tag.objects.all()[:15]], safe=False)


def catalog_view(request):
    qs = products_qs().annotate(reviews_cnt=Count("reviews", distinct=True), avg_rate=Avg("reviews__rate"))
    name_filter = request.GET.get("filter[name]") or request.GET.get("filter") or ""
    if name_filter:
        qs = smart_search_products(qs, name_filter)

    min_price = _decimal(request.GET.get("filter[minPrice]")) if request.GET.get("filter[minPrice]") else None
    max_price = _decimal(request.GET.get("filter[maxPrice]")) if request.GET.get("filter[maxPrice]") else None
    if min_price is not None:
        qs = qs.filter(price__gte=min_price)
    if max_price is not None and max_price > 0:
        qs = qs.filter(price__lte=max_price)

    if request.GET.get("filter[freeDelivery]") == "true":
        qs = qs.filter(free_delivery=True)
    if request.GET.get("filter[available]") == "true":
        qs = qs.filter(count__gt=0)

    category_id = request.GET.get("category")
    if category_id and category_id.isdigit():
        qs = qs.filter(Q(category_id=int(category_id)) | Q(category__parent_id=int(category_id)))

    tag_ids = []
    for key, vals in request.GET.lists():
        if key.startswith("tags"):
            for val in vals:
                tag_ids.extend(int(p) for p in str(val).split(",") if p.strip().isdigit())
    if tag_ids:
        qs = qs.filter(tags__id__in=tag_ids).distinct()

    sort = request.GET.get("sort", "date")
    prefix = "-" if request.GET.get("sortType", "dec") == "dec" else ""
    sort_map = {"price": f"{prefix}price", "reviews": f"{prefix}reviews_cnt",
                "rating": f"{prefix}purchases_count", "date": f"{prefix}date"}
    qs = qs.order_by(sort_map.get(sort, "-date"), "-id")

    paginator = Paginator(qs, _int(request.GET.get("limit"), 20))
    page_obj = paginator.get_page(_int(request.GET.get("currentPage"), 1, maximum=10**6))
    return JsonResponse({
        "items": [serialize_product_short(p) for p in page_obj.object_list],
        "currentPage": page_obj.number,
        "lastPage": paginator.num_pages,
    })


def product_detail_view(request, id):
    product = products_qs().prefetch_related("specifications").filter(pk=id).first()
    if product is None:
        return _not_found()
    return JsonResponse(serialize_product_full(product))


def product_review_view(request, id):
    product = products_qs().filter(pk=id).first()
    if product is None:
        return _not_found()

    if request.method == "POST":
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Unauthorized"}, status=403)
        parsed = parse_body(request)
        data = parsed if isinstance(parsed, dict) else {}
        try:
            rate_val = max(1, min(5, int(data.get("rate") or 5)))
        except (ValueError, TypeError):
            rate_val = 5

        author = str(data.get("author") or request.user.username).strip()[:150] or "Покупатель"
        Review.objects.create(
            product=product,
            author=author,
            email=str(data.get("email") or "user@sokolykoff.shop").strip()[:254] or "user@sokolykoff.shop",
            text=str(data.get("text") or "").strip()[:3000] or "Отличный товар!",
            rate=rate_val,
        )
        # Новый отзыв -> сводка пересчитывается принудительно (force_refresh=True)
        product = products_qs().filter(pk=id).first()
        update_product_ai_summary(product, force_refresh=True)
    return JsonResponse(serialize_reviews(product), safe=False)


def sales_view(request):
    qs = Sale.objects.select_related("product").prefetch_related("product__images").order_by("id")
    paginator = Paginator(qs, 12)
    page_obj = paginator.get_page(_int(request.GET.get("currentPage"), 1, maximum=10**6))
    items = []
    for s in page_obj.object_list:
        p = s.product
        imgs = [serialize_image(img.image, p.title) for img in p.images.all()] or [serialize_image(None, p.title)]
        items.append({
            "id": p.id,
            "price": float(p.price),
            "salePrice": float(s.sale_price),
            "dateFrom": s.date_from.strftime("%d.%m"),
            "dateTo": s.date_to.strftime("%d.%m"),
            "title": p.title,
            "images": imgs,
        })
    return JsonResponse({"items": items, "currentPage": page_obj.number, "lastPage": paginator.num_pages})


def ai_assistant_view(request):
    parsed = parse_body(request)
    data = parsed if isinstance(parsed, dict) else {}
    query = str(data.get("message") or request.GET.get("q") or "")[:300]
    return JsonResponse(ask_shopping_assistant(query, Product.objects.all()))


def healthcheck_view(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    providers = configured_providers()
    return JsonResponse({
        "status": "ok" if db_ok else "degraded",
        "service": "SokolykOff Shop Backend API",
        "version": "2026.11",
        "database": "ok" if db_ok else "unavailable",
        "products_active": Product.objects.count() if db_ok else None,
        "products_in_stock": Product.objects.filter(count__gt=0).count() if db_ok else None,
        "ai_engine": f"llm ({', '.join(providers)}) + local fallback" if llm_enabled() else "local smart engine",
        "docs": "/api/docs/",
    }, status=200 if db_ok else 503)
