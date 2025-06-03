from django.contrib import admin

from .models import (
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
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
    list_display = ("id", "team")
    list_filter = ("team",)
    search_fields = ("messages__human_message_content", "messages__ai_message_content")


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


@admin.register(EvaluationMessage)
class EvaluationMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "human_message_content", "ai_message_content", "context")
    search_fields = ("id",)
