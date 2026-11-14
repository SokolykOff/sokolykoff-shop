import json
import threading
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models.query import QuerySet
from django.test import TestCase, TransactionTestCase

from catalog.models import Category, Product
from . import services
from .models import Order, OrderItem
from .services import OrderError, OutOfStock, cancel_order, create_order
from .tasks import process_order_payment_task


def post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


class BaseOrderTest(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(title="Видеокарты")
        self.product = Product.objects.create(category=self.cat, title="RTX 4060", price=Decimal("295.00"), count=5)
        self.user = User.objects.create_user("buyer", password="pw12345")

    def make_order(self, user=None, count=1):
        order = Order.objects.create(user=user, total_cost=self.product.price * count)
        OrderItem.objects.create(order=order, product=self.product, price=self.product.price, count=count)
        return order


class BasketAndPaymentTests(BaseOrderTest):
    def test_session_basket_add_and_remove(self):
        resp = post(self.client, "/api/basket/", {"id": self.product.id, "count": 2})
        self.assertEqual(resp.json()[0]["count"], 2)
        resp = self.client.delete("/api/basket/", data=json.dumps({"id": self.product.id, "count": 1}),
                                  content_type="application/json")
        self.assertEqual(resp.json()[0]["count"], 1)

    def test_payment_success_and_declined_card(self):
        order = self.make_order(self.user)
        self.client.force_login(self.user)
        bad = post(self.client, f"/api/payment/{order.id}/", {"number": "1111111111111110", "month": "12", "code": "123"})
        self.assertEqual(bad.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.status, "payment_error")
        ok = post(self.client, f"/api/payment/{order.id}/", {"number": "1111111111111112", "month": "12", "code": "123"})
        self.assertEqual(ok.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")

    def test_other_users_order_is_hidden(self):
        order = self.make_order(self.user)
        other = User.objects.create_user("other", password="pw12345")
        self.client.force_login(other)
        self.assertEqual(self.client.get(f"/api/order/{order.id}/").status_code, 404)
        self.assertEqual(post(self.client, f"/api/payment/{order.id}/", {"number": "1111111111111112"}).status_code, 404)
        self.assertEqual(post(self.client, "/api/payment/", {"number": "1111111111111112"}).status_code, 404)

    def test_guest_checkout_flow_is_bound_to_session(self):
        resp = post(self.client, "/api/orders/", [{"id": self.product.id, "count": 2}])
        order_id = resp.json()["orderId"]
        self.assertEqual(self.client.get(f"/api/order/{order_id}/").status_code, 200)
        self.assertEqual(self.client.__class__().get(f"/api/order/{order_id}/").status_code, 404)  # чужая сессия

    def test_paid_order_cannot_be_modified(self):
        order = self.make_order(self.user)
        Order.objects.filter(pk=order.pk).update(status="paid")
        self.client.force_login(self.user)
        self.assertEqual(post(self.client, f"/api/order/{order.id}/", {"city": "X"}).status_code, 409)


class PaymentTaskIdempotencyTests(BaseOrderTest):
    def test_repeated_payment_does_not_duplicate(self):
        order = self.make_order(self.user, count=2)
        first = process_order_payment_task.apply(args=(order.id, "1111111111111112")).get()
        second = process_order_payment_task.apply(args=(order.id, "1111111111111112")).get()
        self.assertEqual(first["status"], "paid")
        self.assertTrue(second["idempotent"])
        self.product.refresh_from_db()
        self.assertEqual(self.product.purchases_count, 2)  # а не 4

    def test_retry_configuration(self):
        t = process_order_payment_task
        self.assertEqual(t.max_retries, 3)
        self.assertIn(OperationalError, t.autoretry_for)
        self.assertTrue(t.acks_late)
        self.assertTrue(t.retry_backoff)

    def test_cancelled_order_is_not_paid(self):
        order = self.make_order(self.user)
        cancel_order(order.id)
        res = process_order_payment_task.apply(args=(order.id, "1111111111111112")).get()
        self.assertEqual(res["status"], "error")


class StockReservationTests(BaseOrderTest):
    def test_order_reserves_stock_and_total(self):
        order = create_order(self.user, [{"id": self.product.id, "count": 2}, {"id": self.product.id, "count": 1}])
        self.product.refresh_from_db()
        self.assertEqual(self.product.count, 2)
        self.assertEqual(order.total_cost, Decimal("885.00"))
        self.assertEqual(order.items.count(), 1)  # дубли позиций схлопнуты

    def test_cannot_oversell_and_nothing_is_persisted(self):
        with self.assertRaises(OutOfStock):
            create_order(self.user, [{"id": self.product.id, "count": 6}])
        self.assertEqual(Order.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.count, 5)

    def test_partial_failure_rolls_back_whole_order(self):
        other = Product.objects.create(category=self.cat, title="Редкая", price=Decimal("10"), count=1)
        with self.assertRaises(OutOfStock):
            create_order(self.user, [{"id": self.product.id, "count": 2}, {"id": other.id, "count": 2}])
        self.product.refresh_from_db()
        self.assertEqual(self.product.count, 5)

    def test_last_item_second_buyer_gets_409_over_http(self):
        Product.objects.filter(pk=self.product.pk).update(count=1)
        self.assertEqual(post(self.client, "/api/orders/", [{"id": self.product.id, "count": 1}]).status_code, 200)
        resp = post(self.client, "/api/orders/", [{"id": self.product.id, "count": 1}])
        self.assertEqual(resp.status_code, 409)
        self.product.refresh_from_db()
        self.assertEqual(self.product.count, 0)

    def test_select_for_update_is_used_inside_atomic_block(self):
        original = QuerySet.select_for_update
        calls = []

        def spy(qs, *a, **kw):
            calls.append(connection.in_atomic_block)
            return original(qs, *a, **kw)

        with mock.patch.object(QuerySet, "select_for_update", spy):
            create_order(self.user, [{"id": self.product.id, "count": 1}])
        self.assertEqual(calls, [True])

    def test_invalid_payloads(self):
        for bad in ([], [{"id": "abc"}], [{"nope": 1}]):
            with self.assertRaises(OrderError):
                create_order(self.user, bad)

    def test_cancel_returns_stock_idempotently(self):
        order = create_order(self.user, [{"id": self.product.id, "count": 3}])
        cancel_order(order.id)
        cancel_order(order.id)
        self.product.refresh_from_db()
        self.assertEqual(self.product.count, 5)


class ConcurrentStockTests(TransactionTestCase):
    """Реальные потоки: 8 покупателей одновременно борются за 3 штуки."""

    def test_parallel_orders_never_oversell(self):
        cat = Category.objects.create(title="GPU")
        product = Product.objects.create(category=cat, title="Последние штуки", price=Decimal("100"), count=3)
        results, barrier = [], threading.Barrier(8)

        def buyer():
            try:
                barrier.wait(timeout=10)
                create_order(None, [{"id": product.pk, "count": 1}])
                results.append("ok")
            except OutOfStock:
                results.append("out")
            except Exception as exc:  # noqa: BLE001
                results.append(f"error: {exc!r}")
            finally:
                connection.close()

        threads = [threading.Thread(target=buyer) for _ in range(8)]
        [t.start() for t in threads]
        [t.join(timeout=60) for t in threads]
        self.assertEqual(sorted(results), ["ok"] * 3 + ["out"] * 5, results)
        product.refresh_from_db()
        self.assertEqual(product.count, 0)
        self.assertEqual(OrderItem.objects.filter(product=product).count(), 3)


class DatabaseInvariantTests(BaseOrderTest):
    def test_database_rejects_negative_stock(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=self.product.pk).update(count=-1)

    def test_database_rejects_zero_quantity(self):
        order = self.make_order(self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            order.items.update(count=0)

    def test_database_rejects_duplicate_order_product(self):
        order = self.make_order(self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderItem.objects.create(order=order, product=self.product, price=10, count=1)


class PostgreSQLLockTests(TransactionTestCase):
    def test_row_lock_blocks_same_product_but_not_another(self):
        self.assertEqual(connection.vendor, "postgresql")
        cat = Category.objects.create(title="Locks")
        first = Product.objects.create(category=cat, title="Locked", price=10, count=1)
        other = Product.objects.create(category=cat, title="Free", price=10, count=1)
        locked = threading.Event()
        release = threading.Event()
        errors = []

        def hold_lock():
            try:
                with transaction.atomic():
                    Product.objects.select_for_update().get(pk=first.pk)
                    locked.set()
                    if not release.wait(10):
                        raise RuntimeError("Lock test timed out")
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()

        thread = threading.Thread(target=hold_lock)
        thread.start()
        try:
            self.assertTrue(locked.wait(5))
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '1s'")
                create_order(None, [{"id": other.pk, "count": 1}])
            with self.assertRaises(OperationalError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '200ms'")
                create_order(None, [{"id": first.pk, "count": 1}])
        finally:
            release.set()
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        create_order(None, [{"id": first.pk, "count": 1}])
