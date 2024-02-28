from django.contrib import admin

from .models import EventAction, EventLog, TimeoutTrigger


class EventLogInline(admin.TabularInline):
    model = EventLog
    extra = 0


@admin.register(TimeoutTrigger)
class TimeoutTriggerAdmin(admin.ModelAdmin):
    inlines = [EventLogInline]


@admin.register(EventAction)
class EventActionAdmin(admin.ModelAdmin):
    pass
