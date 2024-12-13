from django.contrib import admin

from apps.assistants.models import OpenAiAssistant, ToolResources
from apps.experiments.admin import VersionedModelAdminMixin


class ToolResourcesAdmin(admin.TabularInline):
    model = ToolResources


@admin.register(OpenAiAssistant)
class OpenAiAssistantAdmin(VersionedModelAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "team",
        "llm_provider",
        "llm_provider_model",
        "include_file_info",
        "version_family",
        "version_number",
        "is_archived",
    )
    inlines = [ToolResourcesAdmin]
