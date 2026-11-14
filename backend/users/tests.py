import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from catalog.models import Category, Product, Tag
from orders.models import Order
from .models import Profile


class UsersAndSecurityTests(TestCase):
    def test_guest_cannot_post_review(self):
        cat = Category.objects.create(title="Периферия")
        prod = Product.objects.create(category=cat, title="Keychron Q1", price=Decimal("190.00"))
        resp = self.client.post(f"/api/product/{prod.id}/reviews/", data=json.dumps({"text": "x", "rate": 5}),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 403)

    def test_signup_password_change_and_account_deletion(self):
        reg = self.client.post("/api/sign-up/", data=json.dumps(
            {"name": "Denis", "username": "denis_dev", "password": "oldpassword123"}), content_type="application/json")
        self.assertEqual(reg.status_code, 200)
        pw = self.client.post("/api/profile/password/", data=json.dumps(
            {"passwordCurrent": "oldpassword123", "password": "newpassword456"}), content_type="application/json")
        self.assertEqual(pw.status_code, 200)
        self.assertEqual(self.client.post("/api/profile/delete/").status_code, 200)
        self.assertFalse(User.objects.get(username="denis_dev").is_active)

    def test_wrong_current_password_rejected(self):
        User.objects.create_user("u1", password="secret123")
        self.client.login(username="u1", password="secret123")
        resp = self.client.post("/api/profile/password/", data=json.dumps(
            {"passwordCurrent": "WRONG", "password": "newpassword456"}), content_type="application/json")
        self.assertEqual(resp.status_code, 400)


class SoftDeleteModelTests(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(title="Видеокарты")
        self.p = Product.objects.create(category=self.cat, title="RTX", price=Decimal("10"))

    def test_instance_delete_is_soft_and_restorable(self):
        self.p.delete()
        self.assertFalse(Product.objects.filter(pk=self.p.pk).exists())
        archived = Product.all_objects.get(pk=self.p.pk)
        self.assertTrue(archived.is_deleted)
        self.assertIsNotNone(archived.deleted_at)
        archived.restore()
        self.assertTrue(Product.objects.filter(pk=self.p.pk).exists())
        self.assertIsNone(Product.objects.get(pk=self.p.pk).deleted_at)

    def test_queryset_delete_is_soft(self):
        Product.objects.filter(pk=self.p.pk).delete()
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(Product.all_objects.count(), 1)

    def test_hard_delete_really_removes(self):
        Product.objects.hard_delete() if hasattr(Product.objects, "hard_delete") else Product.objects.all().hard_delete()
        self.assertEqual(Product.all_objects.count(), 0)

    def test_deleted_product_hidden_from_api_and_search(self):
        self.p.delete()
        self.assertEqual(self.client.get("/api/product/%d/" % self.p.pk).status_code, 404)
        self.assertEqual(self.client.get("/api/catalog/").json()["items"], [])

    def test_soft_deleted_order_hidden_for_owner(self):
        user = User.objects.create_user("o", password="x12345678")
        order = Order.objects.create(user=user)
        order.delete()
        self.client.force_login(user)
        self.assertEqual(self.client.get(f"/api/order/{order.id}/").status_code, 404)
        self.assertTrue(Order.all_objects.filter(pk=order.pk, is_deleted=True).exists())

    def test_profile_empty_unique_fields_do_not_collide(self):
        a = Profile.objects.create(user=User.objects.create_user("a", password="x12345678"), phone="", email="")
        b = Profile.objects.create(user=User.objects.create_user("b", password="x12345678"), phone="", email="")
        self.assertIsNone(a.phone)
        self.assertIsNone(b.email)


class PublicRepositorySecurityTests(TestCase):
    def test_password_change_requires_current_password(self):
        user = User.objects.create_user('secure_user', password='Original-password-12')
        self.client.force_login(user)
        response = self.client.post('/api/profile/password/', data=json.dumps({'password': 'Replacement-password-34'}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertTrue(user.check_password('Original-password-12'))

    def test_session_mutations_require_csrf(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        response = client.get('/')
        self.assertEqual(response.status_code, 200)
        token = client.cookies['csrftoken'].value
        payload = {'username': 'csrf_user', 'password': 'Unique-password-529!'}
        denied = client.post('/api/sign-up/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(denied.status_code, 403)
        accepted = client.post('/api/sign-up/', data=json.dumps(payload), content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(client.post('/api/profile/delete/').status_code, 403)
        self.assertEqual(client.get('/api/sign-out/').status_code, 405)

    def test_public_reviews_do_not_expose_email(self):
        from catalog.models import Review
        from catalog.views import serialize_reviews
        category = Category.objects.create(title='Demo')
        product = Product.objects.create(category=category, title='Demo', price=Decimal('10'))
        Review.objects.create(product=product, author='Buyer', email='private@example.com', text='Review', rate=5)
        self.assertNotIn('email', serialize_reviews(product)[0])
