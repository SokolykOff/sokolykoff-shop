from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class SoftDeleteQuerySet(models.QuerySet):
    def delete(self):
        return super().update(is_deleted=True, deleted_at=timezone.now())

    def hard_delete(self):
        return super().delete()


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(is_deleted=False)


class SoftDeleteModel(models.Model):
    is_deleted = models.BooleanField("Удалено (в архиве)", default=False)
    deleted_at = models.DateTimeField("Дата удаления", null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at"])

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=["is_deleted", "deleted_at"])


def validate_avatar_size(file):
    if file.size > 2 * 1024 * 1024:
        raise ValidationError("Максимальный размер аватарки — 2 МБ")


class Profile(SoftDeleteModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    full_name = models.CharField("Ф.И.О.", max_length=255, blank=True)
    phone = models.CharField("Телефон", max_length=32, unique=True, null=True, blank=True)
    email = models.EmailField("E-mail", unique=True, null=True, blank=True)
    avatar = models.FileField("Аватар", upload_to="avatars/", null=True, blank=True, validators=[validate_avatar_size])

    class Meta:
        verbose_name = "Профиль"
        verbose_name_plural = "Профили"

    def save(self, *args, **kwargs):
        # Защита от ошибки уникальности пустых строк в БД
        if not self.phone:
            self.phone = None
        if not self.email:
            self.email = None
        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name or self.user.username