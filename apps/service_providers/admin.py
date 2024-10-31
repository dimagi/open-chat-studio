from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from apps.analysis.models import Analysis
from apps.assistants.models import OpenAiAssistant
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
    list_display = ("name", "type", "max_token_limit", "team")
    list_filter = ("team", "type", "name")
    inlines = [ExperimentInline]
    readonly_fields = ["related_experiments", "related_assistants", "related_analyses", "related_nodes"]

    def related_experiments(self, obj):
        experiments = Experiment.objects.filter(llm_provider_model_id=str(obj.id))
        experiment_urls = [
            f"<a href={reverse('admin:experiments_experiment_change',args=[experiment.id])} >{str(experiment)}</a>"
            for experiment in experiments
        ]
        return format_html("<br>".join(experiment_urls))

    related_experiments.short_description = "Experiment Usage"

    def related_assistants(self, obj):
        assistants = OpenAiAssistant.objects.filter(llm_provider_model_id=str(obj.id))
        assistant_urls = [
            f"<a href={reverse('admin:assistants_openaiassistant_change',args=[assistant.id])} >{str(assistant)}</a>"
            for assistant in assistants
        ]
        return format_html("<br>".join(assistant_urls))

    related_assistants.short_description = "Assistant Usage"

    def related_analyses(self, obj):
        analyses = Analysis.objects.filter(llm_provider_model_id=str(obj.id))
        analysis_urls = [
            f"<a href={reverse('admin:analysis_analysis_change',args=[analysis.id])} >{str(analyses)}</a>"
            for analysis in analyses
        ]
        return format_html("<br>".join(analysis_urls))

    related_assistants.short_description = "Analysis Usage"

    def related_nodes(self, obj):
        nodes = Node.objects.filter(params__llm_provider_model_id=str(obj.id))
        pipelines = set(node.pipeline for node in nodes)
        pipeline_urls = [
            f"<a href={reverse('admin:pipelines_pipeline_change',args=[pipeline.id])} >{str(pipeline)}</a>"
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
