from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html_join
from django.utils.safestring import SafeString

from apps.pipelines.models import Node
from apps.utils.admin import ReadonlyAdminMixin

from .models import (
    EmbeddingProviderModel,
    LlmProvider,
    LlmProviderModel,
    MessagingProvider,
    TraceProvider,
    VoiceProvider,
)


@admin.register(LlmProvider)
class ServiceConfigAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")


@admin.register(LlmProviderModel)
class LlmProviderModelAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "type", "max_token_limit", "team")
    list_filter = ("team", "type", "name")
    readonly_fields = ["related_nodes"]

    def related_nodes(self, obj):
        nodes = Node.objects.filter(params__llm_provider_model_id=str(obj.id))
        pipelines = {node.pipeline for node in nodes}
        return format_html_join(
            SafeString("<br>"),
            '<a href="{}">{}</a>',
            ((reverse("admin:pipelines_pipeline_change", args=[p.id]), str(p)) for p in pipelines),
        )

    related_nodes.short_description = "Pipeline Usage"


@admin.register(VoiceProvider)
class VoiceProviderAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")


@admin.register(MessagingProvider)
class MessagingProviderAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")


@admin.register(TraceProvider)
class TraceProviderAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")


@admin.register(EmbeddingProviderModel)
class EmbeddingProviderModelAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")
