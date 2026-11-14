from django.contrib import admin
from .models import Profile


@admin.action(description="♻️ Восстановить выбранные элементы из архива")
def restore_items(modeladmin, request, queryset):
    queryset.update(is_deleted=False, deleted_at=None)


class SoftDeleteAdmin(admin.ModelAdmin):
    actions = [restore_items]

    def get_queryset(self, request):
        return self.model.all_objects.all()


@admin.register(Profile)
class ProfileAdmin(SoftDeleteAdmin):
    list_display = ("id", "user", "full_name", "phone", "email", "is_deleted")
    list_filter = ("is_deleted",)
    search_fields = ("full_name", "phone", "email")