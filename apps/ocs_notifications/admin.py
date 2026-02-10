from django.contrib import admin

from .models import Notification, NotificationMute, UserNotification, UserNotificationPreferences


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "level", "team", "last_event_at", "identifier")
    list_filter = ("level", "team")
    search_fields = ("title", "message", "identifier")


@admin.register(UserNotification)
class UserNotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "notification", "read", "read_at", "team")
    list_filter = ("read", "team")
    search_fields = ("user__email", "notification__title")


@admin.register(UserNotificationPreferences)
class UserNotificationPreferencesAdmin(admin.ModelAdmin):
    list_display = ("user", "team", "in_app_enabled", "email_enabled")
    list_filter = ("in_app_enabled", "email_enabled", "team")
    search_fields = ("user__email",)


@admin.register(NotificationMute)
class NotificationMuteAdmin(admin.ModelAdmin):
    list_display = ("user", "team", "notification_identifier", "muted_until", "is_active")
    list_filter = ("team", "muted_until")
    search_fields = ("user__email", "notification_identifier")

    def is_active(self, obj):
        return obj.is_active()

    is_active.boolean = True
    is_active.short_description = "Active"
