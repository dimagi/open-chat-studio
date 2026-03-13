from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from apps.assistants.models import OpenAiAssistant
from apps.pipelines.models import Node

from .models import (
    EmbeddingProviderModel,
    LlmProvider,
    LlmProviderModel,
    MessagingProvider,
    TraceProvider,
    VoiceProvider,
)


@admin.register(LlmProvider)
class ServiceConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")


@admin.register(LlmProviderModel)
class LlmProviderModelAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "max_token_limit", "team")
    list_filter = ("team", "type", "name")
    readonly_fields = ["related_assistants", "related_nodes"]

    def related_assistants(self, obj):
        assistants = OpenAiAssistant.objects.filter(llm_provider_model_id=str(obj.id))
        assistant_urls = [
            f"<a href={reverse('admin:assistants_openaiassistant_change', args=[assistant.id])} >{str(assistant)}</a>"
            for assistant in assistants
        ]
        return format_html("<br>".join(assistant_urls))

    related_assistants.short_description = "Assistant Usage"

    def related_nodes(self, obj):
        nodes = Node.objects.filter(params__llm_provider_model_id=str(obj.id))
        pipelines = set(node.pipeline for node in nodes)
        pipeline_urls = [
            f"<a href={reverse('admin:pipelines_pipeline_change', args=[pipeline.id])} >{str(pipeline)}</a>"
            for pipeline in pipelines
        ]
        return format_html("<br>".join(pipeline_urls))

    related_nodes.short_description = "Pipeline Usage"


@admin.register(VoiceProvider)
class VoiceProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")


@admin.register(MessagingProvider)
class MessagingProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")


@admin.register(TraceProvider)
class TraceProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")


@admin.register(EmbeddingProviderModel)
class EmbeddingProviderModelAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")
    readonly_fields = ("team",)
