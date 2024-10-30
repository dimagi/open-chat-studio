from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from apps.experiments.models import Experiment
from apps.pipelines.models import Node

from .models import LlmProvider, LlmProviderModel, MessagingProvider, TraceProvider, VoiceProvider


@admin.register(LlmProvider)
class ServiceConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "type")
    list_filter = ("team", "type")


class ExperimentInline(admin.TabularInline):
    model = Experiment
    extra = 0
    fields = ("name", "llm_provider")
    readonly_fields = ("name", "llm_provider")
    can_delete = False


@admin.register(LlmProviderModel)
class LlmProviderModelAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "max_token_limit", "team", "related_nodes")
    list_filter = ("team", "type", "name")
    inlines = [ExperimentInline]

    def related_nodes(self, obj):
        nodes = Node.objects.filter(params__llm_provider_model_id=str(obj.id))
        pipelines = set(node.pipeline for node in nodes)
        pipeline_urls = [
            f"<a href={reverse('admin:pipelines_pipeline_change',args=[pipeline.id])} >{pipeline.name}</a>"
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
