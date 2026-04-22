from django.contrib import admin

from apps.utils.admin import ReadonlyAdminMixin

from ..experiments.models import ExperimentSession
from .models import Chat, ChatAttachment, ChatMessage


class ExperimentSessionInline(ReadonlyAdminMixin, admin.TabularInline):
    model = ExperimentSession
    fields = (
        "created_at",
        "experiment",
        "participant",
    )
    can_delete = False
    extra = 0
    show_change_link = True


class ChatMessageInline(ReadonlyAdminMixin, admin.TabularInline):
    model = ChatMessage
    fields = ("created_at", "message_type", "content", "metadata")
    can_delete = False
    extra = 0
    show_change_link = True


class ChatAttachmentInline(ReadonlyAdminMixin, admin.TabularInline):
    model = ChatAttachment
    fields = ("created_at", "tool_type", "files")
    can_delete = False
    extra = 0
    show_change_link = True


@admin.register(Chat)
class ChatAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("participant", "team", "created_at", "updated_at")
    search_fields = ("experiment_session__participant__identifier",)
    list_filter = ("team",)
    date_hierarchy = "created_at"
    inlines = [
        ExperimentSessionInline,
        ChatAttachmentInline,
        ChatMessageInline,
    ]

    @admin.display(description="Participant")
    def participant(self, obj):
        return obj.experiment_session.participant.identifier


@admin.register(ChatMessage)
class ChatMessageAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("chat", "team", "message_type", "content", "created_at", "updated_at")
    search_fields = (
        "chat__id",
        "content",
    )
    list_filter = ("message_type",)
    date_hierarchy = "created_at"

    @admin.display(description="Team")
    def team(self, obj):
        return obj.chat.team.name


@admin.register(ChatAttachment)
class ChatAttachmentAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("chat", "team", "tool_type", "created_at")
    list_filter = ("tool_type",)
    date_hierarchy = "created_at"

    @admin.display(description="Team")
    def team(self, obj):
        return obj.chat.team.name
