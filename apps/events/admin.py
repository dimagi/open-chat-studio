from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.experiments.admin import VersionedModelAdminMixin

from .models import EventAction, EventLog, ScheduledMessage, StaticTrigger, TimeoutTrigger


class EventLogInline(GenericTabularInline):
    model = EventLog
    extra = 0


@admin.register(TimeoutTrigger)
class TimeoutTriggerAdmin(VersionedModelAdminMixin, admin.ModelAdmin):
    list_display = (
        "action_type",
        "experiment",
        "delay",
        "total_num_triggers",
        "version_family",
        "is_archived",
    )
    inlines = [EventLogInline]

    @admin.display(description="Action")
    def action_type(self, obj):
        if obj.action:
            return obj.action.action_type
        return ""

    def _get_working_version_label(self, working_version):
        return working_version.id

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return super().get_queryset(request).select_related("action", "experiment")


@admin.register(StaticTrigger)
class StaticTriggerAdmin(VersionedModelAdminMixin, admin.ModelAdmin):
    list_display = (
        "type",
        "experiment",
        "action_type",
        "version_family",
        "is_archived",
    )
    inlines = [EventLogInline]

    @admin.display(description="Action")
    def action_type(self, obj):
        if obj.action:
            return obj.action.action_type
        return ""

    def _get_working_version_label(self, working_version):
        return working_version.id

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return super().get_queryset(request).select_related("action", "experiment")


@admin.register(EventAction)
class EventActionAdmin(admin.ModelAdmin):
    list_display = ["action_type"]


@admin.register(ScheduledMessage)
class ScheduledMessageAdmin(admin.ModelAdmin):
    list_display = ["name", "external_id", "is_complete"]
