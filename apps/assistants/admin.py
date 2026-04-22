from django.contrib import admin

from apps.assistants.models import OpenAiAssistant, ToolResources
from apps.experiments.admin import VersionedModelAdminMixin
from apps.utils.admin import ReadonlyAdminMixin


class ToolResourcesAdmin(ReadonlyAdminMixin, admin.TabularInline):
    model = ToolResources


@admin.register(OpenAiAssistant)
class OpenAiAssistantAdmin(ReadonlyAdminMixin, VersionedModelAdminMixin, admin.ModelAdmin):
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
