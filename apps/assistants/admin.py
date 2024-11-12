from django.contrib import admin

from apps.assistants.models import OpenAiAssistant, ToolResources


class ToolResourcesAdmin(admin.TabularInline):
    model = ToolResources


@admin.register(OpenAiAssistant)
class OpenAiAssistantAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "llm_provider", "llm_provider_model", "include_file_info")
    inlines = [ToolResourcesAdmin]
