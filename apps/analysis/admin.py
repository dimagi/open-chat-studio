from django.contrib import admin

from .models import Analysis, Resource


@admin.register(Analysis)
class AnalysisAdmin(admin.ModelAdmin):
    list_display = ("name", "source", "pipelines", "llm_provider")
    search_fields = ("name",)


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "content_size")
    list_filter = ("type",)
    search_fields = ("name",)
    readonly_fields = ("content_size",)
