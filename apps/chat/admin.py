from django.contrib import admin

from .models import Chat, ChatMessage


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ("user", "team", "created_at", "updated_at")
    search_fields = ("user",)
    list_filter = (
        "team",
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
    list_display = ("chat", "team", "message_type", "content", "created_at", "updated_at")
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

    @admin.display(description="Team")
    def team(self, obj):
        return obj.chat.team.name
