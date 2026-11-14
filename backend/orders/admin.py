from django.contrib import admin, messages
from users.admin import SoftDeleteAdmin
from .models import Order, OrderItem, DeliverySettings
from .services import OrderError, cancel_order


@admin.action(description="❌ Отменить заказ и вернуть товары на склад")
def cancel_orders(modeladmin, request, queryset):
    done = 0
    for order in queryset:
        try:
            cancel_order(order.pk)
            done += 1
        except OrderError as exc:
            modeladmin.message_user(request, f"Заказ #{order.pk}: {exc}", level=messages.WARNING)
    modeladmin.message_user(request, f"Отменено заказов: {done}")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(SoftDeleteAdmin):
    list_display = ("id", "full_name", "total_cost", "delivery_type", "payment_type", "status", "created_at", "is_deleted")
    list_filter = ("status", "delivery_type", "payment_type", "is_deleted")
    inlines = [OrderItemInline]
    actions = SoftDeleteAdmin.actions + [cancel_orders]


@admin.register(DeliverySettings)
class DeliverySettingsAdmin(admin.ModelAdmin):
    list_display = ("free_delivery_threshold", "ordinary_delivery_cost", "express_delivery_cost")