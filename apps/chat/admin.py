from django.contrib import admin

from .models import Chat, ChatMessage, FutureMessage


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "updated_at")
    search_fields = ("user",)
    list_filter = (
        "user",
        "created_at",
        "updated_at",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("chat", "message_type", "content", "created_at", "updated_at")
    search_fields = (
        "chat",
        "content",
    )
    list_filter = (
        "message_type",
        "created_at",
        "updated_at",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(FutureMessage)
class FutureMessageAdmin(admin.ModelAdmin):
    list_display = ("message", "due_at", "end_date", "resolved")
    search_fields = ("resolved",)
