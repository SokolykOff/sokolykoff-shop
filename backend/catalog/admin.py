from django.contrib import admin
from users.admin import SoftDeleteAdmin
from .models import Category, Tag, Product, ProductImage, ProductSpecification, Review, Sale


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


class ProductSpecificationInline(admin.TabularInline):
    model = ProductSpecification
    extra = 2


@admin.register(Category)
class CategoryAdmin(SoftDeleteAdmin):
    list_display = ("id", "title", "parent", "is_featured", "sort_index", "is_deleted")
    list_filter = ("is_featured", "is_deleted")
    search_fields = ("title",)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)


@admin.register(Product)
class ProductAdmin(SoftDeleteAdmin):
    list_display = ("id", "title", "category", "price", "count", "limited_edition", "purchases_count", "is_deleted")
    list_filter = ("category", "free_delivery", "limited_edition", "is_deleted")
    search_fields = ("title", "description", "search_keywords")
    inlines = [ProductImageInline, ProductSpecificationInline]


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("id", "product", "author", "rate", "date")
    list_filter = ("rate",)


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("id", "product", "sale_price", "date_from", "date_to")