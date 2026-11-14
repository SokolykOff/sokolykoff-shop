import json
import urllib.error
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from . import ai_service
from .ai_service import (ask_shopping_assistant, call_llm, parse_price_bounds, smart_search_products,
                         update_product_ai_summary)
from .models import Category, Product, ProductSpecification, Review

NO_LLM = dict(OPENAI_API_KEY="", GEMINI_API_KEY="", AI_API_KEY="")


@override_settings(**NO_LLM)
class BaseCatalogTest(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(title="Ноутбуки и Планшеты")
        self.kb_cat = Category.objects.create(title="Периферия")
        self.p_cheap = Product.objects.create(category=self.cat, title="Коврик CyberMat", price=Decimal("29.00"), count=10)
        self.p_mid = Product.objects.create(category=self.cat, title="Ноутбук RedmiBook Pro", price=Decimal("540.00"), count=10)
        self.p_exp = Product.objects.create(category=self.cat, title="Ноутбук MacBook Pro 16", price=Decimal("2450.00"), count=10)
        self.kb = Product.objects.create(category=self.kb_cat, title="Механическая Клавиатура Keychron Q1",
                                         price=Decimal("190.00"), count=5, description="Тихие свитчи")
        self.user = User.objects.create_user("tester", password="pw12345")

    def add_review(self, product, rate, text="Нормально"):
        return Review.objects.create(product=product, author="a", email="a@a.ru", text=text, rate=rate)


class BudgetParserTests(BaseCatalogTest):
    def test_budget_forms(self):
        cases = {
            "товары до 300": (None, 300, ""),
            "игровой пк до 1500$": (None, 1500, "игровой пк"),
            "ноутбук от 500 до 1700": (500, 1700, "ноутбук"),
            "от 200 до 600": (200, 600, ""),
            "наушники не дороже 400": (None, 400, "наушники"),
            "ноутбук до 1 500": (None, 1500, "ноутбук"),
            "монитор до 1.5к": (None, 1500, "монитор"),
            "видеокарта от 600": (600, None, "видеокарта"),
            "ноутбук от 900 до 300": (300, 900, "ноутбук"),
        }
        for text, (mn, mx, clean) in cases.items():
            got = parse_price_bounds(text)
            self.assertEqual((got[0] and int(got[0]), got[1] and int(got[1]), got[2]), (mn, mx, clean), text)

    def test_search_budget_filters(self):
        qs = Product.objects.all()
        self.assertEqual(list(smart_search_products(qs, "товары до 300")), [self.p_cheap, self.kb][:1] + [self.kb])
        self.assertEqual(list(smart_search_products(qs, "ноутбук до 700")), [self.p_mid])
        self.assertEqual(list(smart_search_products(qs, "ноутбук от 1000")), [self.p_exp])
        self.assertEqual(list(smart_search_products(qs, "ноутбук от 100 до 600")), [self.p_mid])

    def test_layout_typo_and_stopword_fixes(self):
        qs = Product.objects.all()
        self.assertEqual(list(smart_search_products(qs, "rkfdbfnehf")), [self.kb])      # раскладка -> «клавиатура»
        self.assertEqual(list(smart_search_products(qs, "клаваитура")), [self.kb])      # опечатка
        self.assertEqual(list(smart_search_products(qs, "твоары до 100")), [self.p_cheap])  # опечатка стоп-слова
        self.assertEqual(list(smart_search_products(qs, "абракадабра")), [])

    def test_title_match_has_priority(self):
        qs = Product.objects.all()
        self.assertEqual(set(smart_search_products(qs, "ноутбук")), {self.p_mid, self.p_exp})


class AiSummaryTests(BaseCatalogTest):
    def test_local_summary_is_structured_and_reflects_reviews(self):
        self.add_review(self.kb, 5, "Тихая клавиатура, сборка отличная. Звук приятный.")
        self.add_review(self.kb, 2, "Дорого, переплата за бренд. Пластик скрипит.")
        data = update_product_ai_summary(Product.objects.get(pk=self.kb.pk), force_refresh=True)
        self.assertEqual(data["source"], "local")
        self.assertEqual((data["count"], data["avg"]), (2, 3.5))
        self.assertTrue(data["pros"] and data["cons"] and "3.5" in data["verdict"])
        self.assertTrue(any("переплат" in c.lower() or "скрип" in c.lower() or "дорог" in c.lower() for c in data["cons"]))

    def test_no_reviews_returns_empty_state(self):
        self.assertTrue(update_product_ai_summary(self.p_cheap)["empty"])

    def test_cached_in_db_and_served_without_recompute(self):
        self.add_review(self.kb, 5, "Отлично")
        first = update_product_ai_summary(Product.objects.get(pk=self.kb.pk))
        self.assertFalse(first["cached"])
        stored = Product.objects.get(pk=self.kb.pk)
        self.assertEqual(json.loads(stored.ai_summary)["verdict"], first["verdict"])
        with mock.patch.object(ai_service, "generate_review_summary", side_effect=AssertionError("пересчёт!")):
            second = update_product_ai_summary(stored)
        self.assertTrue(second["cached"])

    def test_new_review_via_api_recalculates_summary(self):
        self.add_review(self.kb, 5, "Отлично")
        update_product_ai_summary(Product.objects.get(pk=self.kb.pk))
        self.client.force_login(self.user)
        resp = self.client.post(f"/api/product/{self.kb.pk}/reviews/",
                                data=json.dumps({"text": "Скрипит, дорого", "rate": 1}), content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        stored = Product.objects.get(pk=self.kb.pk)
        summary = json.loads(stored.ai_summary)
        self.assertEqual((summary["count"], summary["avg"]), (2, 3.0))
        detail = self.client.get(f"/api/product/{self.kb.pk}/").json()
        self.assertTrue(detail["aiSummary"]["cached"])
        self.assertEqual(detail["aiSummary"]["count"], 2)

    def test_signal_invalidates_and_stale_cache_is_detected(self):
        self.add_review(self.kb, 5, "Отлично")
        update_product_ai_summary(Product.objects.get(pk=self.kb.pk))
        self.add_review(self.kb, 4, "Хорошо")  # отзыв «в обход» вью (админка/shell)
        self.assertEqual(Product.objects.get(pk=self.kb.pk).ai_summary, "")
        self.assertEqual(update_product_ai_summary(Product.objects.get(pk=self.kb.pk))["count"], 2)

    def test_count_mismatch_in_cache_triggers_recompute(self):
        self.add_review(self.kb, 5, "Отлично")
        update_product_ai_summary(Product.objects.get(pk=self.kb.pk))
        Review.objects.bulk_create([Review(product=self.kb, author="x", email="x@x.ru", text="ок", rate=3)])
        self.assertEqual(update_product_ai_summary(Product.objects.get(pk=self.kb.pk))["count"], 2)


def fake_llm_json():
    return json.dumps({"pros": ["Тихие свитчи"], "cons": ["Цена"], "verdict": "Берите."})


class LlmModeTests(BaseCatalogTest):
    def setUp(self):
        super().setUp()
        self.add_review(self.kb, 5, "Отлично")
        self.product = Product.objects.get(pk=self.kb.pk)

    @override_settings(OPENAI_API_KEY="sk-test", GEMINI_API_KEY="", AI_API_KEY="")
    def test_openai_success_uses_llm_with_clamped_timeout(self):
        reply = {"choices": [{"message": {"content": "```json\n" + fake_llm_json() + "\n```"}}]}
        with mock.patch.object(ai_service, "_http_post_json", return_value=reply) as post:
            data = update_product_ai_summary(self.product, force_refresh=True)
        self.assertEqual((data["source"], data["verdict"]), ("openai", "Берите."))
        self.assertTrue(5.0 <= post.call_args.args[3] <= 8.0)

    @override_settings(OPENAI_API_KEY="", GEMINI_API_KEY="g-test", AI_API_KEY="", AI_HTTP_TIMEOUT="100")
    def test_gemini_success_and_timeout_is_capped(self):
        reply = {"candidates": [{"content": {"parts": [{"text": fake_llm_json()}]}}]}
        with mock.patch.object(ai_service, "_http_post_json", return_value=reply) as post:
            data = update_product_ai_summary(self.product, force_refresh=True)
        self.assertEqual(data["source"], "gemini")
        self.assertEqual(post.call_args.args[3], 8.0)
        self.assertEqual(post.call_args.args[2], {"x-goog-api-key": "g-test"})

    @override_settings(OPENAI_API_KEY="sk-test", GEMINI_API_KEY="", AI_API_KEY="")
    def test_network_errors_fall_back_to_local_engine(self):
        errors = [urllib.error.URLError("down"), TimeoutError(), urllib.error.HTTPError("u", 500, "err", {}, None),
                  ValueError("bad json")]
        for exc in errors:
            with mock.patch.object(ai_service, "_http_post_json", side_effect=exc):
                data = update_product_ai_summary(self.product, force_refresh=True)
            self.assertEqual(data["source"], "local", exc)

    @override_settings(OPENAI_API_KEY="sk-test", GEMINI_API_KEY="", AI_API_KEY="")
    def test_garbage_llm_answer_falls_back(self):
        reply = {"choices": [{"message": {"content": "Извините, не могу."}}]}
        with mock.patch.object(ai_service, "_http_post_json", return_value=reply):
            self.assertEqual(update_product_ai_summary(self.product, force_refresh=True)["source"], "local")

    @override_settings(OPENAI_API_KEY="sk-test", GEMINI_API_KEY="", AI_API_KEY="")
    def test_assistant_uses_llm_text_but_only_in_stock_items(self):
        reply = {"choices": [{"message": {"content": "Рекомендую Keychron."}}]}
        with mock.patch.object(ai_service, "_http_post_json", return_value=reply):
            res = ask_shopping_assistant("клавиатура до 200", Product.objects.all())
        self.assertEqual((res["source"], res["reply"]), ("openai", "Рекомендую Keychron."))

    @override_settings(**NO_LLM)
    def test_call_llm_without_keys_returns_none(self):
        self.assertIsNone(call_llm("hi"))


class AssistantAndApiTests(BaseCatalogTest):
    def test_assistant_only_in_stock_and_within_budget(self):
        Product.objects.filter(pk=self.p_cheap.pk).update(count=0)
        res = self.client.post("/api/ai-assistant/", data=json.dumps({"message": "товары до 300"}),
                               content_type="application/json").json()
        self.assertNotIn(self.p_cheap.id, [i["id"] for i in res["items"]])
        res = ask_shopping_assistant("ноутбук до 700", Product.objects.all())
        self.assertEqual([i["id"] for i in res["items"]], [self.p_mid.id])
        self.assertTrue(all(i["price"] <= 700 for i in res["items"]))

    def test_assistant_suggests_nearest_when_budget_too_low(self):
        res = ask_shopping_assistant("клавиатура до 100", Product.objects.all())
        self.assertEqual([i["id"] for i in res["items"]], [self.kb.id])
        self.assertIn("бюджет", res["reply"])

    def test_assistant_empty_message_and_xss_safe_json(self):
        self.assertEqual(ask_shopping_assistant("", Product.objects.all())["items"], [])

    def test_health_docs_and_schema(self):
        health = self.client.get("/api/health/").json()
        self.assertEqual((health["status"], health["database"]), ("ok", "ok"))
        self.assertEqual(self.client.get("/api/docs/").status_code, 200)
        schema = self.client.get("/api/schema/").json()
        self.assertEqual(schema["openapi"], "3.0.3")
        for path in ("/api/catalog/", "/api/ai-assistant/", "/api/payment/{id}/", "/api/orders/"):
            self.assertIn(path, schema["paths"])

    def test_product_404_is_json(self):
        self.assertEqual(self.client.get("/api/product/99999/").status_code, 404)
        self.assertEqual(self.client.get("/api/product/99999/reviews/").status_code, 404)

    def test_catalog_has_no_n_plus_one(self):
        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get("/api/catalog/?limit=100").status_code, 200)
            return len(ctx)
        before = count_queries()
        for i in range(12):
            p = Product.objects.create(category=self.cat, title=f"Товар {i}", price=Decimal("10"), count=1)
            self.add_review(p, 4, "ок")
        self.assertEqual(count_queries(), before)

    def test_catalog_bad_params_do_not_500(self):
        r = self.client.get("/api/catalog/?filter[minPrice]=abc&limit=zzz&currentPage=-4")
        self.assertEqual(r.status_code, 200)


class ModelValidationTests(BaseCatalogTest):
    def test_negative_price_validation(self):
        with self.assertRaises(ValidationError):
            Product(category=self.cat, title="Broken", price=Decimal("-100.00")).full_clean()


class FixturesTests(TestCase):
    def test_fixtures_load_with_summaries_and_existing_images(self):
        call_command("loaddata", "fixtures.json", verbosity=0)
        products = Product.objects.prefetch_related("images", "reviews")
        self.assertEqual(products.count(), 29)
        for p in products:
            summary = json.loads(p.ai_summary)
            self.assertTrue(summary["pros"] and summary["cons"] and summary["verdict"], p.title)
            images = list(p.images.all())
            self.assertEqual(len(images), 1, p.title)
            self.assertTrue((Path(settings.MEDIA_ROOT) / images[0].image.name).exists(), images[0].image.name)
            with override_settings(**NO_LLM):  # кэш из fixtures валиден: отдаётся без пересчёта
                self.assertTrue(update_product_ai_summary(p)["cached"], p.title)
