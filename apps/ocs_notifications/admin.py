from django.contrib import admin

from apps.utils.admin import ReadonlyAdminMixin

from .models import NotificationChannel


@admin.register(NotificationChannel)
class NotificationChannelAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("team", "channel_name", "messaging_provider", "level", "enabled", "created_at", "updated_at")
    search_fields = ("channel_name", "team__name")
    list_filter = ("enabled", "level")
