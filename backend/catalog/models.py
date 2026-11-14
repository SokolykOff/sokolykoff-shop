from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from users.models import SoftDeleteModel


class Category(SoftDeleteModel):
    title = models.CharField("Название категории", max_length=200)
    image = models.FileField("Иконка", upload_to="categories/", null=True, blank=True)
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True,
        related_name="subcategories", verbose_name="Родительская категория"
    )
    is_featured = models.BooleanField("Избранная (на главной)", default=False)
    sort_index = models.PositiveIntegerField("Индекс сортировки", default=0)

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"
        ordering = ["sort_index", "title"]

    def clean(self):
        if self.parent and self.parent.parent:
            raise ValidationError("Максимальная вложенность категорий — 2 уровня.")

    def __str__(self):
        return f"{self.parent.title} -> {self.title}" if self.parent else self.title


class Tag(models.Model):
    name = models.CharField("Тег", max_length=100, unique=True)

    class Meta:
        verbose_name = "Тег"
        verbose_name_plural = "Теги"

    def __str__(self):
        return self.name


class Product(SoftDeleteModel):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="products", verbose_name="Категория")
    title = models.CharField("Название", max_length=255)
    description = models.TextField("Краткое описание", blank=True)
    full_description = models.TextField("Полное описание", blank=True)
    price = models.DecimalField(
        "Цена", max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))]
    )
    count = models.PositiveIntegerField("Количество на складе", default=10)
    date = models.DateTimeField("Дата создания", auto_now_add=True)
    free_delivery = models.BooleanField("Бесплатная доставка", default=False)
    limited_edition = models.BooleanField("Ограниченный тираж", default=False)
    sort_index = models.PositiveIntegerField("Индекс сортировки", default=0)
    purchases_count = models.PositiveIntegerField("Количество покупок", default=0)
    tags = models.ManyToManyField(Tag, blank=True, related_name="products", verbose_name="Теги")
    search_keywords = models.TextField("Ключевые смыслы (для умного поиска)", blank=True)
    ai_summary = models.TextField("ИИ-выжимка по отзывам", blank=True)
    ai_updated_at = models.DateTimeField("Дата обновления ИИ-анализа", null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(price__gte=Decimal("0.01")), name="product_price_positive"),
        ]
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ["sort_index", "-purchases_count"]

    def __str__(self):
        return f"{self.title} (${self.price})"


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.FileField("Изображение", upload_to="products/")
    alt = models.CharField("Alt-текст", max_length=200, blank=True)


class ProductSpecification(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="specifications")
    name = models.CharField("Характеристика", max_length=150)
    value = models.CharField("Значение", max_length=255)


class Review(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    author = models.CharField("Автор", max_length=150)
    email = models.EmailField("Email")
    text = models.TextField("Текст отзыва")
    rate = models.PositiveSmallIntegerField(
        "Оценка", validators=[MinValueValidator(1), MaxValueValidator(5)], default=5
    )
    date = models.DateTimeField("Дата", auto_now_add=True)

    class Meta:
        verbose_name = "Отзыв"
        verbose_name_plural = "Отзывы"
        ordering = ["-date"]


class Sale(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="sales")
    sale_price = models.DecimalField(
        "Цена по скидке", max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))]
    )
    date_from = models.DateField("Начало акции")
    date_to = models.DateField("Конец акции")

    class Meta:
        verbose_name = "Акция / Скидка"
        verbose_name_plural = "Акции и Скидки"

    def clean(self):
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValidationError("Дата окончания акции не может быть раньше даты начала.")