"""Инвалидация кэша ``Product.ai_summary`` при любом изменении отзывов (вью, админка, shell)."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .ai_service import invalidate_product_ai_summary
from .models import Review


@receiver([post_save, post_delete], sender=Review, dispatch_uid="review_invalidate_ai_summary")
def invalidate_summary_on_review_change(sender, instance, raw=False, **kwargs):
    if raw:  # loaddata: fixtures уже содержат согласованный ai_summary
        return
    invalidate_product_ai_summary(instance.product_id)
