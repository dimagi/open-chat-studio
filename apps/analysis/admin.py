from django.contrib import admin

from apps.utils.admin import ReadonlyAdminMixin

from .models import AnalysisQuery, TranscriptAnalysis


class AnalysisQueryAdmin(ReadonlyAdminMixin, admin.TabularInline):
    model = AnalysisQuery
    fields = (
        "name",
        "prompt",
        "output_format",
    )
    can_delete = False
    extra = 0
    show_change_link = True


@admin.register(TranscriptAnalysis)
class TranscriptAnalysisAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "experiment",
        "created_by",
        "status",
    )
    search_fields = ("name", "description")
    list_filter = ("status",)
    ordering = ("-created_at",)
    inlines = (AnalysisQueryAdmin,)
