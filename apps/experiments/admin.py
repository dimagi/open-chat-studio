from urllib.parse import quote as urlquote

from django.contrib import admin
from django.contrib.admin.utils import quote
from django.db.models import QuerySet
from django.http import HttpRequest
from django.urls import reverse
from django.utils.html import format_html

from apps.experiments import models


class VersionedModelAdminMixin:
    @admin.display(description="Version Family")
    def version_family(self, obj):
        if obj.working_version:
            label = self._get_working_version_label(obj.working_version)
            return self._get_object_link(obj.working_version, label)
        return ""

    def _get_working_version_label(self, working_version):
        return getattr(working_version, "name", "")

    def _get_object_link(self, obj, link_text=None):
        """Copied from django.contrib.admin"""
        opts = obj._meta
        obj_url = reverse(
            f"admin:{opts.app_label}_{opts.model_name}_change",
            args=(quote(obj.pk),),
            current_app=self.admin_site.name,
        )
        return format_html('<a href="{}">{}</a>', urlquote(obj_url), link_text or str(obj))

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return self.model.objects.get_all().select_related("working_version")


@admin.register(models.PromptBuilderHistory)
class PromptBuilderHistoryAdmin(admin.ModelAdmin):
    list_display = ("history", "created_at", "owner")
    list_filter = ("owner",)


@admin.register(models.SourceMaterial)
class SourceMaterialAdmin(VersionedModelAdminMixin, admin.ModelAdmin):
    list_display = (
        "topic",
        "team",
        "owner",
        "version_family",
        "is_archived",
    )
    list_filter = (
        "team",
        "owner",
    )


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
    list_display = ("participant", "experiment")
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
class ExperimentAdmin(VersionedModelAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "team",
        "owner",
        "source_material",
        "llm_provider",
        "llm_provider_model",
        "version_family",
        "version_number",
        "is_archived",
    )
    list_filter = ("team", "owner", "source_material")
    readonly_fields = ("public_id",)
    search_fields = ("public_id", "name")


@admin.register(models.ExperimentRoute)
class ExperimentRouteAdmin(VersionedModelAdminMixin, admin.ModelAdmin):
    list_display = (
        "parent",
        "child",
        "keyword",
        "is_default",
        "version_family",
        "is_archived",
    )


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
        "version_family",
        "is_archived",
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
