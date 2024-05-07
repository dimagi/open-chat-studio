from django.contrib import admin

from apps.assistants.models import OpenAiAssistant, ToolResources


class ToolResourcesAdmin(admin.TabularInline):
    model = ToolResources


@admin.register(OpenAiAssistant)
class OpenAiAssistantAdmin(admin.ModelAdmin):
    inlines = [ToolResourcesAdmin]
