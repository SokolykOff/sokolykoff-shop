"""Бизнес-логика заказов: атомарное резервирование остатков и их возврат.

Защита от гонок (два покупателя одновременно выкупают последний товар):
* всё выполняется в ``transaction.atomic()``;
* строки товаров блокируются ``select_for_update()`` в детерминированном порядке (по pk —
  это снижает риск взаимных блокировок между параллельными заказами);
* остаток проверяется и списывается уже под блокировкой, поэтому ``Product.count`` не
  может уйти в минус. Используется PostgreSQL с уровнем изоляции READ COMMITTED."""
from collections import OrderedDict
from decimal import Decimal

from django.db import transaction
from django.db.models import F

from catalog.models import Product
from .models import Order, OrderItem

CANCELLABLE_STATUSES = ("accepted", "confirmed", "payment_error")


class OrderError(Exception):
    """Ошибка бизнес-логики заказа (отдаётся клиенту как 4xx)."""
    status = 400


class OutOfStock(OrderError):
    status = 409


def normalize_items(raw_items):
    """[{'id': 1, 'count': 2}, ...] -> OrderedDict(pk -> суммарное количество)."""
    items = OrderedDict()
    for el in raw_items or []:
        if not isinstance(el, dict) or "id" not in el:
            continue
        try:
            pk = int(el["id"])
            count = int(el.get("count", 1) or 1)
        except (TypeError, ValueError):
            raise OrderError("Некорректные данные корзины")
        items[pk] = items.get(pk, 0) + max(1, count)
    return items


@transaction.atomic
def create_order(user, raw_items, profile=None):
    items = normalize_items(raw_items)
    if not items:
        raise OrderError("Корзина пуста")

    # Блокируем строки в порядке pk — одинаковый порядок снижает риск deadlock между заказами
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=items.keys()).order_by("pk")}
    for pk, count in items.items():
        product = products.get(pk)
        if product is None:
            raise OrderError(f"Товар #{pk} недоступен")
        if product.count < count:
            raise OutOfStock(f"Недостаточно товара «{product.title}» на складе: доступно {product.count} шт.")

    order = Order.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        full_name=profile.full_name if profile else "",
        email=(profile.email or "") if profile else "",
        phone=(profile.phone or "") if profile else "",
    )
    total = Decimal("0.00")
    for pk, count in items.items():
        product = products[pk]
        OrderItem.objects.create(order=order, product=product, price=product.price, count=count)
        Product.objects.filter(pk=pk).update(count=F("count") - count)  # списание остатка под блокировкой
        total += product.price * count
    order.total_cost = total
    order.save(update_fields=["total_cost"])
    return order


@transaction.atomic
def cancel_order(order_id):
    """Отмена заказа с возвратом остатков на склад. Идемпотентна: повторный вызов ничего не меняет."""
    order = Order.all_objects.select_for_update().get(pk=order_id)
    if order.status == "cancelled":
        return order
    if order.status not in CANCELLABLE_STATUSES:
        raise OrderError("Оплаченный заказ нельзя отменить автоматически")
    for item in order.items.select_related("product").order_by("product_id"):
        Product.all_objects.filter(pk=item.product_id).update(count=F("count") + item.count)
    order.status = "cancelled"
    order.save(update_fields=["status"])
    return order
