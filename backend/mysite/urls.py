from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, re_path, include
from django.views.generic import TemplateView

from catalog import views as catalog_views
from users import views as users_views
from orders import views as orders_views
from . import api_docs

urlpatterns = [
    path("admin/", admin.site.urls),
    re_path(r"^api/ai-assistant/?$", catalog_views.ai_assistant_view),
    re_path(r"^api/health/?$", catalog_views.healthcheck_view),
    path("api/schema/", api_docs.schema_view, name="api-schema"),
    path("api/docs/", api_docs.docs_view, name="api-docs"),

    # API Каталога и Главной (работают и со слэшем, и без него)
    re_path(r"^api/categories/?$", catalog_views.categories_view),
    re_path(r"^api/banners/?$", catalog_views.banners_view),
    re_path(r"^api/products/popular/?$", catalog_views.popular_products_view),
    re_path(r"^api/products/limited/?$", catalog_views.limited_products_view),
    re_path(r"^api/catalog/?$", catalog_views.catalog_view),
    re_path(r"^api/product/(?P<id>\d+)/?$", catalog_views.product_detail_view),
    re_path(r"^api/product/(?P<id>\d+)/reviews?/?$", catalog_views.product_review_view),
    re_path(r"^api/sales/?$", catalog_views.sales_view),
    re_path(r"^api/tags/?$", catalog_views.tags_view),

    # API Корзины, Заказов и Оплаты
    re_path(r"^api/basket/?$", orders_views.basket_view),
    re_path(r"^api/orders/?$", orders_views.orders_view),
    re_path(r"^api/orders?/(?P<id>\d+)/?$", orders_views.order_detail_view),
    re_path(r"^api/payment(?:/(?P<id>\d+))?/?$", orders_views.payment_view),
    re_path(r"^api/account/?$", orders_views.account_view),

    # API Пользователей и Профиля
    re_path(r"^api/sign-in/?$", users_views.sign_in_view),
    re_path(r"^api/sign-up/?$", users_views.sign_up_view),
    re_path(r"^api/sign-out/?$", users_views.sign_out_view),
    re_path(r"^api/profile/?$", users_views.profile_view),
    re_path(r"^api/profile/avatar/?$", users_views.profile_avatar_view),
    re_path(r"^api/profile/password/?$", users_views.profile_password_view),
    re_path(r"^api/profile/delete/?$", users_views.delete_account_view),

    # Страницы фронтенда без слэша на конце
    path("catalog/<int:id>", TemplateView.as_view(template_name="frontend/catalog.html")),
    path("product/<int:id>", TemplateView.as_view(template_name="frontend/product.html")),
    path("orders/<int:id>", TemplateView.as_view(template_name="frontend/order.html")),
    path("payment/<int:id>", TemplateView.as_view(template_name="frontend/payment.html")),
    re_path(r"^historyview/?$", TemplateView.as_view(template_name="frontend/catalog.html")),

    path("", include("frontend.urls")),
]

# Раздача media в dev-режиме (в production — nginx/Caddy или объектное хранилище)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
