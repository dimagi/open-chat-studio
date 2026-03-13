from django.contrib import admin

from apps.utils.admin import ReadonlyAdminMixin

from .models import Trace


@admin.register(Trace)
class TraceAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("timestamp", "experiment", "session", "participant")
