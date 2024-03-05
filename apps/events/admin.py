from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline

from .models import EventAction, EventLog, StaticTrigger, TimeoutTrigger


class EventLogInline(GenericTabularInline):
    model = EventLog
    extra = 0


@admin.register(TimeoutTrigger)
class TimeoutTriggerAdmin(admin.ModelAdmin):
    inlines = [EventLogInline]


@admin.register(StaticTrigger)
class StaticTriggerAdmin(admin.ModelAdmin):
    inlines = [EventLogInline]


@admin.register(EventAction)
class EventActionAdmin(admin.ModelAdmin):
    pass
