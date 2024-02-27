from django.contrib import admin

from .models import EventAction, TimeoutTrigger, TriggerStats


class TriggerStatsInline(admin.TabularInline):
    model = TriggerStats
    extra = 0


@admin.register(TimeoutTrigger)
class TimeoutTriggerAdmin(admin.ModelAdmin):
    inlines = [TriggerStatsInline]


@admin.register(EventAction)
class EventActionAdmin(admin.ModelAdmin):
    pass
