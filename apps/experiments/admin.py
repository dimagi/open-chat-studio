from django.contrib import admin

from apps.experiments import models


@admin.register(models.PromptBuilderHistory)
class PromptBuilderHistoryAdmin(admin.ModelAdmin):
    list_display = ("history", "created_at", "owner")
    list_filter = ("owner",)


@admin.register(models.SourceMaterial)
class SourceMaterialAdmin(admin.ModelAdmin):
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
class SafetyLayerAdmin(admin.ModelAdmin):
    list_display = (
        "team",
        "name",
        "messages_to_review",
    )
    list_filter = ("team",)


@admin.register(models.Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("identifier", "team", "public_id")
    readonly_fields = ("public_id",)
    list_filter = ("team",)
    search_fields = ("external_chat_id",)


@admin.register(models.ParticipantData)
class ParticipantData(admin.ModelAdmin):
    list_display = ("participant", "content_type", "object_id")
    readonly_fields = ("data",)
    list_filter = ("participant",)


@admin.register(models.Survey)
class SurveyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "team",
        "url",
    )
    list_filter = ("team",)


@admin.register(models.Experiment)
class ExperimentAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "owner", "source_material", "llm", "llm_provider")
    list_filter = ("team", "owner", "source_material")
    inlines = [SafetyLayerInline]
    exclude = ["safety_layers"]
    readonly_fields = ("public_id",)


@admin.register(models.ExperimentRoute)
class ExperimentRouteAdmin(admin.ModelAdmin):
    list_display = ("parent", "child", "keyword", "is_default")


@admin.register(models.ExperimentSession)
class ExperimentSessionAdmin(admin.ModelAdmin):
    list_display = (
        "experiment",
        "team",
        "participant",
        "status",
        "created_at",
        "llm",
    )
    search_fields = ("public_id", "experiment__name", "participant__identifier")
    list_filter = ("created_at", "status", "team")
    readonly_fields = ("public_id",)

    @admin.display(description="Team")
    def team(self, obj):
        return obj.experiment.team.name


@admin.register(models.ConsentForm)
class ConsentFormAdmin(admin.ModelAdmin):
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


@admin.register(models.NoActivityMessageConfig)
class NoActivityMessageConfigAdmin(admin.ModelAdmin):
    list_display = (
        "team",
        "message_for_bot",
        "name",
        "max_pings",
    )
    list_filter = ("team",)
