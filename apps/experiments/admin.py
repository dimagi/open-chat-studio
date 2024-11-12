from django.contrib import admin
from django.db.models.query import QuerySet
from django.http import HttpRequest

from apps.experiments import models


class VersionedModelAdminMixin:
    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return self.model.objects.get_all()


@admin.register(models.PromptBuilderHistory)
class PromptBuilderHistoryAdmin(admin.ModelAdmin):
    list_display = ("history", "created_at", "owner")
    list_filter = ("owner",)


@admin.register(models.SourceMaterial)
class SourceMaterialAdmin(VersionedModelAdminMixin, admin.ModelAdmin):
    list_display = ("topic", "team", "owner")
    list_filter = (
        "team",
        "owner",
    )


class SafetyLayerInline(admin.TabularInline):
    model = models.Experiment.safety_layers.through
    extra = 1
    # If needed, add fields to be shown in the inline form:
    # fields = ('prompt', )
    # autocomplete_fields = ['author']


@admin.register(models.SafetyLayer)
class SafetyLayerAdmin(VersionedModelAdminMixin, admin.ModelAdmin):
    list_display = (
        "team",
        "name",
        "messages_to_review",
    )
    list_filter = ("team",)


class ParticipantDataInline(admin.TabularInline):
    model = models.ParticipantData


@admin.register(models.Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("identifier", "team", "public_id", "platform")
    readonly_fields = ("public_id",)
    list_filter = ("team", "platform")
    search_fields = ("external_chat_id",)
    inlines = [ParticipantDataInline]


@admin.register(models.ParticipantData)
class ParticipantData(admin.ModelAdmin):
    list_display = ("participant", "content_type", "object_id")
    list_filter = ("participant",)


@admin.register(models.Survey)
class SurveyAdmin(VersionedModelAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "team",
        "url",
    )
    list_filter = ("team",)


@admin.register(models.Experiment)
class ExperimentAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "team",
        "owner",
        "source_material",
        "llm_provider",
        "llm_provider_model",
        "version_family",
        "version_number",
    )
    list_filter = ("team", "owner", "source_material")
    inlines = [SafetyLayerInline]
    exclude = ["safety_layers"]
    readonly_fields = ("public_id",)

    @admin.display(description="Version Family")
    def version_family(self, obj):
        if obj.working_version:
            return obj.working_version.name
        return obj.name


@admin.register(models.ExperimentRoute)
class ExperimentRouteAdmin(VersionedModelAdminMixin, admin.ModelAdmin):
    list_display = ("parent", "child", "keyword", "is_default")


@admin.register(models.ExperimentSession)
class ExperimentSessionAdmin(admin.ModelAdmin):
    list_display = (
        "experiment",
        "team",
        "participant",
        "status",
        "created_at",
    )
    search_fields = ("external_id", "experiment__name", "participant__identifier")
    list_filter = ("created_at", "status", "team")
    readonly_fields = ("external_id",)

    @admin.display(description="Team")
    def team(self, obj):
        return obj.experiment.team.name


@admin.register(models.ConsentForm)
class ConsentFormAdmin(VersionedModelAdminMixin, admin.ModelAdmin):
    list_display = (
        "team",
        "name",
        "consent_text",
        "is_default",
    )
    readonly_fields = ("is_default",)
    list_filter = ("team",)


@admin.register(models.SyntheticVoice)
class SyntheticVoiceAdmin(admin.ModelAdmin):
    list_display = (
        "service",
        "name",
        "language",
        "get_gender",
        "neural",
        "team",
    )
    list_filter = ("service", "language", "gender")

    @admin.display(description="Team")
    def team(self, obj):
        return obj.voice_provider.team.name if obj.voice_provider else ""
