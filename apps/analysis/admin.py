from django.contrib import admin

from .models import AnalysisQuery, TranscriptAnalysis


class ReadonlyAdminMixin:
    def get_readonly_fields(self, request, obj=None):
        return list(
            set(
                [field.name for field in self.opts.local_fields]
                + [field.name for field in self.opts.local_many_to_many]
            )
        )


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
