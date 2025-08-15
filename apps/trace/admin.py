from django.contrib import admin

from apps.utils.admin import ReadonlyAdminMixin

from .models import Span, Trace


@admin.register(Trace)
class TraceAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("timestamp", "experiment", "session", "participant")


@admin.register(Span)
class SpanAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("span_id", "trace", "parent_span", "name", "start_time", "end_time", "status")
