from django.contrib import admin

from .models import Analysis, AnalysisRun, Resource, RunGroup


@admin.register(Analysis)
class AnalysisAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "source", "pipeline", "llm_provider", "llm_model")
    search_fields = ("name",)


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "type", "content_size")
    list_filter = ("type",)
    search_fields = ("name",)
    readonly_fields = ("content_size",)


class AnalysisRunInline(admin.TabularInline):
    model = AnalysisRun
    fields = ("id", "status", "start_time", "end_time")


@admin.register(RunGroup)
class RunGroupAdmin(admin.ModelAdmin):
    list_display = ("id", "team", "analysis", "start_time", "end_time", "status")
    list_filter = ("status",)
    search_fields = ("analysis__name",)
    inlines = [
        AnalysisRunInline,
    ]


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "start_time", "end_time", "status")
    list_filter = ("status",)
