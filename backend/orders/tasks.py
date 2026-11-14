import logging

from celery import shared_task
from django.db import OperationalError, transaction
from django.db.models import F

from catalog.models import Product
from .models import Order

logger = logging.getLogger(__name__)

PAYMENT_DECLINED = "Платежный шлюз отклонил транзакцию (некорректный номер или оканчивается на 0)"


@shared_task(
    bind=True,
    autoretry_for=(OperationalError,),  # временные сбои БД (блокировки, обрыв соединения)
    retry_backoff=True,                 # 1s, 2s, 4s ... экспоненциальная пауза
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,                     # подтверждение брокеру — только после выполнения
    name="orders.process_order_payment",
)
def process_order_payment_task(self, order_id, number):
    """Асинхронная оплата заказа. Идемпотентна: повторный вызов для уже оплаченного заказа
    (ретрай брокера, двойной клик, дубль сообщения) ничего не дублирует."""
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if not order:
            return {"status": "error", "error": "Заказ не найден"}

        if order.status == "paid":
            logger.info("Заказ %s уже оплачен — повторная оплата проигнорирована", order.pk)
            return {"status": "paid", "order_id": order.pk, "idempotent": True}
        if order.status == "cancelled":
            return {"status": "error", "error": "Заказ отменён"}

        clean_number = "".join(ch for ch in str(number) if ch.isdigit())
        if len(clean_number) >= 8 and not clean_number.endswith("0"):
            order.status = "paid"
            order.payment_error = None
            order.save(update_fields=["status", "payment_error"])
            for item in order.items.order_by("product_id"):
                Product.all_objects.filter(pk=item.product_id).update(
                    purchases_count=F("purchases_count") + item.count
                )
            return {"status": "paid", "order_id": order.pk}

        order.status = "payment_error"
        order.payment_error = PAYMENT_DECLINED
        order.save(update_fields=["status", "payment_error"])
        return {"status": "payment_error", "error": PAYMENT_DECLINED}
