from decimal import Decimal

from django.http import JsonResponse

from catalog.models import Product
from catalog.views import PRODUCT_PREFETCH, serialize_product_short
from users.models import Profile
from users.views import parse_body, serialize_image
from .models import DeliverySettings, Order
from .services import OrderError, create_order

ORDER_PREFETCH = ("items__product__images", "items__product__tags", "items__product__reviews")


def _body_dict(request):
    parsed = parse_body(request)
    return parsed if isinstance(parsed, dict) else {}


def _to_int(value, default=1):
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def get_basket_response(request):
    basket = request.session.get("basket", {})
    product_ids = [int(pid) for pid in basket.keys() if str(pid).isdigit()]
    products = Product.objects.filter(id__in=product_ids).prefetch_related(*PRODUCT_PREFETCH)
    return JsonResponse([serialize_product_short(p, count_override=basket[str(p.id)]) for p in products], safe=False)


def basket_view(request):
    basket = request.session.get("basket", {})
    if request.method in ("POST", "DELETE"):
        data = _body_dict(request)
        pid = str(data.get("id", ""))
        count = max(1, _to_int(data.get("count"), 1))
        if request.method == "POST" and pid.isdigit():
            basket[pid] = basket.get(pid, 0) + count
        elif request.method == "DELETE" and pid in basket:
            basket[pid] -= count
            if basket[pid] <= 0:
                del basket[pid]
        request.session["basket"] = basket
        request.session.modified = True
    return get_basket_response(request)


def serialize_order(order):
    """Ожидает order, загруженный с ORDER_PREFETCH (иначе будет по запросу на позицию)."""
    profile = getattr(order.user, "profile", None) if order.user else None
    return {
        "id": order.id,
        "orderId": order.id,
        "createdAt": order.created_at.strftime("%Y-%m-%d %H:%M"),
        "fullName": order.full_name or (profile.full_name if profile else ""),
        "email": order.email or (profile.email if profile else ""),
        "phone": order.phone or (profile.phone if profile else ""),
        "deliveryType": order.delivery_type,
        "paymentType": order.payment_type,
        "totalCost": float(order.total_cost),
        "status": order.status,
        "city": order.city,
        "address": order.address,
        "paymentError": order.payment_error,
        "products": [serialize_product_short(i.product, count_override=i.count) for i in order.items.all()],
    }


def _orders_qs():
    return Order.objects.select_related("user__profile").prefetch_related(*ORDER_PREFETCH)


def get_owned_order(request, order_id):
    """Заказ доступен владельцу (user) или гостю, создавшему его в этой сессии. Иначе — None
    (404, а не 403: не раскрываем существование чужих заказов)."""
    if not order_id or not str(order_id).isdigit():
        return None
    order = _orders_qs().filter(pk=int(order_id)).first()
    if order is None:
        return None
    if request.user.is_authenticated and (order.user_id == request.user.id or request.user.is_staff):
        return order
    if order.user_id is None and order.id in request.session.get("guest_orders", []):
        return order
    return None


def account_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Unauthorized"}, status=401)
    profile, _ = Profile.objects.get_or_create(user=request.user)
    parts = (profile.full_name or request.user.username).split()
    orders = _orders_qs().filter(user=request.user)[:5]
    return JsonResponse({
        "surname": parts[0] if len(parts) > 0 else "",
        "firstname": parts[1] if len(parts) > 1 else "",
        "secondname": parts[2] if len(parts) > 2 else "",
        "avatar": serialize_image(profile.avatar, profile.full_name),
        "orders": [serialize_order(o) for o in orders],
    })


def orders_view(request):
    if request.method == "GET":
        if not request.user.is_authenticated:
            return JsonResponse([], safe=False)
        return JsonResponse([serialize_order(o) for o in _orders_qs().filter(user=request.user)], safe=False)

    if request.method == "POST":
        items_data = parse_body(request)
        if isinstance(items_data, dict):
            items_data = list(items_data.values())
        profile = getattr(request.user, "profile", None) if request.user.is_authenticated else None
        try:
            order = create_order(request.user, items_data, profile=profile)  # атомарно + select_for_update
        except OrderError as exc:
            return JsonResponse({"error": str(exc)}, status=exc.status)
        if order.user_id is None:
            request.session["guest_orders"] = [*request.session.get("guest_orders", []), order.id]
        return JsonResponse({"orderId": order.id})
    return JsonResponse({"error": "Method not allowed"}, status=405)


def order_detail_view(request, id):
    order = get_owned_order(request, id)
    if order is None:
        return JsonResponse({"error": "Заказ не найден"}, status=404)

    if request.method == "GET":
        return JsonResponse(serialize_order(order))

    if request.method == "POST":
        if order.status in ("paid", "cancelled"):
            return JsonResponse({"error": "Заказ уже оплачен или отменён и не может быть изменён"}, status=409)
        data = _body_dict(request)
        order.full_name = data.get("fullName") or order.full_name
        order.phone = data.get("phone") or order.phone
        order.email = data.get("email") or order.email
        order.delivery_type = data.get("deliveryType") or "ordinary"
        order.payment_type = data.get("paymentType") or "online"
        order.city = data.get("city") or ""
        order.address = data.get("address") or ""

        settings_obj = DeliverySettings.get_solo()
        items_sum = sum((i.price * i.count for i in order.items.all()), Decimal("0.00"))
        delivery_cost = Decimal("0.00")
        if order.delivery_type == "express":
            delivery_cost = settings_obj.express_delivery_cost
        elif items_sum < settings_obj.free_delivery_threshold:
            delivery_cost = settings_obj.ordinary_delivery_cost

        order.total_cost = items_sum + delivery_cost
        order.status = "confirmed"
        order.save()
        return JsonResponse({"orderId": order.id})
    return JsonResponse({"error": "Method not allowed"}, status=405)


def payment_view(request, id=None):
    from .tasks import process_order_payment_task

    data = _body_dict(request)
    order = get_owned_order(request, id or data.get("orderId") or data.get("id"))
    if order is None:
        return JsonResponse({"error": "Заказ не найден"}, status=404)

    number = "".join(ch for ch in str(data.get("number", "")) if ch.isdigit())
    month = "".join(ch for ch in str(data.get("month", "")) if ch.isdigit())
    code = "".join(ch for ch in str(data.get("code", "")) if ch.isdigit())

    if len(number) < 8:
        return JsonResponse({"error": "Некорректный номер карты"}, status=400)
    if month and not (1 <= int(month) <= 12):
        return JsonResponse({"error": "Некорректный месяц"}, status=400)
    if code and len(code) != 3:
        return JsonResponse({"error": "CVV должен состоять из 3 цифр"}, status=400)

    try:
        async_res = process_order_payment_task.delay(order.id, number)
        Order.objects.filter(pk=order.id).update(payment_task_id=getattr(async_res, "id", "") or "eager-task")
    except Exception:  # noqa: BLE001 — брокер недоступен: оплачиваем синхронно (задача идемпотентна)
        process_order_payment_task.apply(args=(order.id, number))

    order.refresh_from_db()
    if order.status == "paid":
        request.session["basket"] = {}
        request.session.modified = True
        return JsonResponse({"status": "ok", "taskId": order.payment_task_id})
    if order.status in ("accepted", "confirmed") and not order.payment_error:
        # реальный Celery-воркер ещё обрабатывает платёж — клиент опрашивает статус заказа
        return JsonResponse({"status": "processing", "taskId": order.payment_task_id}, status=202)
    return JsonResponse({"error": order.payment_error or "Ошибка оплаты"}, status=400)
