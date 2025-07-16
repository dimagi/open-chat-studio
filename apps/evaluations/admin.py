from django.contrib import admin

from apps.utils.admin import ReadonlyAdminMixin

from .models import (
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationResult,
    EvaluationRun,
    Evaluator,
)


@admin.register(Evaluator)
class EvaluatorAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "type", "team")
    list_filter = ("type", "team")
    search_fields = ("type",)


@admin.register(EvaluationDataset)
class EvaluationDatasetAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "name", "team")
    list_filter = ("team",)


@admin.register(EvaluationConfig)
class EvaluationConfigAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "name", "dataset", "team")
    list_filter = ("team",)
    filter_horizontal = ("evaluators",)
    search_fields = ("name",)


class EvaluationResultInline(ReadonlyAdminMixin, admin.TabularInline):
    model = EvaluationResult
    extra = 0
    readonly_fields = ("evaluator", "output")


@admin.register(EvaluationRun)
class EvaluationRunAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "config", "created_at", "finished_at", "user", "team")
    list_filter = ("team",)
    readonly_fields = ("created_at", "finished_at")
    inlines = (EvaluationResultInline,)


@admin.register(EvaluationResult)
class EvaluationResultAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "run", "evaluator", "team")
    list_filter = ("evaluator", "team")
    readonly_fields = ("output",)
    search_fields = ("output",)


@admin.register(EvaluationMessage)
class EvaluationMessageAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "input", "output", "context")
    search_fields = ("id",)
