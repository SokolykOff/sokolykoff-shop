from decimal import Decimal
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models
from catalog.models import Product
from users.models import SoftDeleteModel


class DeliverySettings(models.Model):
    free_delivery_threshold = models.DecimalField(
        "Порог бесплатной доставки ($)", max_digits=10, decimal_places=2,
        default=Decimal("2000.00"), validators=[MinValueValidator(Decimal("0.00"))]
    )
    ordinary_delivery_cost = models.DecimalField(
        "Цена обычной доставки ($)", max_digits=10, decimal_places=2,
        default=Decimal("200.00"), validators=[MinValueValidator(Decimal("0.00"))]
    )
    express_delivery_cost = models.DecimalField(
        "Цена экспресс-доставки ($)", max_digits=10, decimal_places=2,
        default=Decimal("500.00"), validators=[MinValueValidator(Decimal("0.00"))]
    )

    class Meta:
        verbose_name = "Настройки доставки"
        verbose_name_plural = "Настройки доставки"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Order(SoftDeleteModel):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    created_at = models.DateTimeField("Дата оформления", auto_now_add=True)
    full_name = models.CharField("Ф.И.О.", max_length=255, blank=True)
    email = models.EmailField("E-mail", blank=True)
    phone = models.CharField("Телефон", max_length=32, blank=True)
    delivery_type = models.CharField("Тип доставки", max_length=50, default="ordinary")
    payment_type = models.CharField("Способ оплаты", max_length=50, default="online")
    total_cost = models.DecimalField(
        "Итоговая сумма", max_digits=12, decimal_places=2,
        default=Decimal("0.00"), validators=[MinValueValidator(Decimal("0.00"))]
    )
    status = models.CharField("Статус", max_length=50, default="accepted")
    city = models.CharField("Город", max_length=100, blank=True)
    address = models.TextField("Адрес", blank=True)
    payment_error = models.CharField("Ошибка оплаты", max_length=255, null=True, blank=True)
    payment_task_id = models.CharField("ID задачи оплаты Celery", max_length=100, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "-created_at"], name="order_user_created_idx")]
        constraints = [
            models.CheckConstraint(condition=models.Q(total_cost__gte=0), name="order_total_nonnegative"),
        ]
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        ordering = ["-created_at"]


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_items")
    price = models.DecimalField(
        "Цена на момент покупки", max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))]
    )
    count = models.PositiveIntegerField("Количество", default=1, validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(count__gte=1), name="order_item_count_positive"),
            models.CheckConstraint(condition=models.Q(price__gte=Decimal("0.01")), name="order_item_price_positive"),
            models.UniqueConstraint(fields=["order", "product"], name="order_product_unique"),
        ]
