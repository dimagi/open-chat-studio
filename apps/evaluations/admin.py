from django.contrib import admin

from .models import (
    EvaluationConfig,
    EvaluationDataset,
    EvaluationResult,
    EvaluationRun,
    Evaluator,
)


@admin.register(Evaluator)
class EvaluatorAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "team")
    list_filter = ("type", "team")
    search_fields = ("type",)


@admin.register(EvaluationDataset)
class EvaluationDatasetAdmin(admin.ModelAdmin):
    list_display = ("id", "message_type", "team")
    list_filter = ("message_type", "team")
    filter_horizontal = ("sessions",)
    search_fields = ("version__version_number",)


@admin.register(EvaluationConfig)
class EvaluationConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "dataset", "team")
    list_filter = ("team",)
    filter_horizontal = ("evaluators",)
    search_fields = ("name",)


class EvaluationResultInline(admin.TabularInline):
    model = EvaluationResult
    extra = 0
    readonly_fields = ("evaluator", "output")


@admin.register(EvaluationRun)
class EvaluationRunAdmin(admin.ModelAdmin):
    list_display = ("id", "config", "created_at", "finished_at", "user", "team")
    list_filter = ("team",)
    readonly_fields = ("created_at", "finished_at")
    inlines = (EvaluationResultInline,)


@admin.register(EvaluationResult)
class EvaluationResultAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "evaluator", "team")
    list_filter = ("evaluator", "team")
    readonly_fields = ("output",)
    search_fields = ("output",)
