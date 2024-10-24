from django.contrib import admin

from apps.experiments.models import Experiment

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
    list_display = ("name", "type", "max_token_limit", "supports_tool_calling", "team")
    list_filter = ("team", "type", "name")
    inlines = [ExperimentInline]


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
