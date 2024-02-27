from django.contrib import admin

from .models import EventAction, TimeoutTrigger


@admin.register(TimeoutTrigger)
class TimeoutTriggerAdmin(admin.ModelAdmin):
    readonly_fields = (
        "last_triggered",
        "trigger_count",
    )


@admin.register(EventAction)
class EventActionAdmin(admin.ModelAdmin):
    pass
