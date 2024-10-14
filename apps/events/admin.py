from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline

from apps.experiments.admin import VersionedModelAdminMixin

from .models import EventAction, EventLog, ScheduledMessage, StaticTrigger, TimeoutTrigger


class EventLogInline(GenericTabularInline):
    model = EventLog
    extra = 0


@admin.register(TimeoutTrigger)
class TimeoutTriggerAdmin(VersionedModelAdminMixin, admin.ModelAdmin):
    inlines = [EventLogInline]


@admin.register(StaticTrigger)
class StaticTriggerAdmin(VersionedModelAdminMixin, admin.ModelAdmin):
    inlines = [EventLogInline]


@admin.register(EventAction)
class EventActionAdmin(admin.ModelAdmin):
    pass


@admin.register(ScheduledMessage)
class ScheduledMessageAdmin(admin.ModelAdmin):
    list_display = ["name", "external_id", "is_complete"]
