from django.contrib import admin

from .models import Chat, ChatMessage


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ("participant", "team", "created_at", "updated_at")
    search_fields = ("experiment_session__participant__identifier",)
    list_filter = (
        "team",
        "created_at",
        "updated_at",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )

    @admin.display(description="Participant")
    def participant(self, obj):
        return obj.experiment_session.participant.identifier


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("chat", "team", "message_type", "content", "created_at", "updated_at")
    search_fields = (
        "chat__id",
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
