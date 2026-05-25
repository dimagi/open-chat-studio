import logging
from collections import defaultdict

from django import views as django_views
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from django_tables2 import SingleTableView

from apps.assistants.models import OpenAiAssistant
from apps.experiments.models import Experiment
from apps.files.forms import get_file_formset
from apps.files.views import BaseAddFileHtmxView
from apps.service_providers.forms import LlmProviderModelForm
from apps.service_providers.models import (
    EmbeddingProviderModel,
    LlmProviderModel,
    VoiceProvider,
    VoiceProviderType,
)
from apps.utils.deletion import get_related_objects

from ..generics.chips import Chip
from ..generics.referenced_objects import render_referenced_objects_modal
from ..teams.decorators import login_and_team_required
from ..teams.mixins import LoginAndTeamRequiredMixin
from .usages import get_provider_usages
from .utils import ServiceProvider, get_available_subtypes, get_service_provider_forms

log = logging.getLogger("ocs.service_providers")


def _lookup_subtype_by_slug(subtype_enum, slug):
    """Find the enum member whose ``str()`` form is ``slug``."""
    for member in subtype_enum:
        if str(member) == slug:
            return member
    raise KeyError(slug)


class ServiceProviderMixin:
    @property
    def provider_type(self) -> ServiceProvider:
        type_ = self.kwargs["provider_type"]
        return ServiceProvider[type_]


class ServiceProviderUsagesView(
    LoginAndTeamRequiredMixin, ServiceProviderMixin, PermissionRequiredMixin, django_views.View
):
    template_name = "service_providers/usages.html"

    def get_permission_required(self):
        return (self.provider_type.get_permission("view"),)

    def get(self, request, *args, **kwargs):
        provider = get_object_or_404(self.provider_type.model, team=request.team, pk=self.kwargs["pk"])
        usages = get_provider_usages(provider)
        return render(
            request,
            self.template_name,
            {
                "provider": provider,
                "provider_type": self.provider_type,
                "usages": usages,
                "title": f"Usages of {provider.name}",
                "active_tab": "manage-team",
            },
        )


class ServiceProviderTableView(
    LoginAndTeamRequiredMixin, SingleTableView, ServiceProviderMixin, PermissionRequiredMixin
):
    template_name = "table/single_table.html"

    def get_permission_required(self):
        return (self.provider_type.get_permission("view"),)

    def get_queryset(self):
        return self.provider_type.model.objects.filter(team=self.request.team)

    def get_table_class(self):
        return self.provider_type.table


def matches_blocking_deletion_condition(obj):
    return (getattr(obj, "working_version_id", None) is None) or (getattr(obj, "is_default_version", False) is True)


@require_http_methods(["DELETE"])
@login_and_team_required
def delete_service_provider(request, team_slug: str, provider_type: str, pk: int):
    provider = ServiceProvider[provider_type]
    if not request.user.has_perm(provider.get_permission("delete")):
        raise PermissionDenied()
    service_config = get_object_or_404(provider.model, team=request.team, pk=pk)
    related_objects = get_related_objects(service_config)

    if related_objects:
        filtered_objects = [obj for obj in related_objects if matches_blocking_deletion_condition(obj)]
        related_experiments = [
            Chip(
                label=(
                    f"{experiment.name} [{experiment.get_version_name()}]"
                    if experiment.is_working_version
                    else f"{experiment.name} {experiment.get_version_name()} [published]"
                ),
                url=experiment.get_absolute_url(),
            )
            for experiment in [obj for obj in filtered_objects if isinstance(obj, Experiment)]
        ]
        related_assistants = [
            Chip(label=assistant.name, url=assistant.get_absolute_url())
            for assistant in [obj for obj in filtered_objects if isinstance(obj, OpenAiAssistant)]
        ]
        if related_experiments or related_assistants:
            return render_referenced_objects_modal(
                "service provider",
                experiments=related_experiments,
                assistants=related_assistants,
            )
    service_config.delete()
    return HttpResponse()


class AddFileToProvider(BaseAddFileHtmxView):
    @transaction.atomic()
    def form_valid(self, form):
        provider = ServiceProvider[self.kwargs["provider_type"]]
        provider = get_object_or_404(provider.model, team__slug=self.request.team.slug, pk=self.kwargs["pk"])
        file = super().form_valid(form)
        provider.add_files([file])
        return file

    def get_delete_url(self, file):
        provider = ServiceProvider[self.kwargs["provider_type"]]
        return reverse(
            "service_providers:delete_file",
            kwargs={
                "team_slug": self.request.team.slug,
                "provider_type": provider.slug,
                "pk": self.kwargs["pk"],
                "file_id": file.id,
            },
        )


@login_required
@permission_required("files.delete_file")
@transaction.atomic()
def remove_file(request, team_slug: str, provider_type: str, pk: int, file_id: int):
    provider = ServiceProvider[provider_type]
    service_config = get_object_or_404(provider.model, team=request.team, pk=pk)
    service_config.remove_file(file_id)
    return HttpResponse()


class CreateServiceProvider(
    LoginAndTeamRequiredMixin, django_views.View, ServiceProviderMixin, PermissionRequiredMixin
):
    template_name = "service_providers/provider_form.html"

    def get_permission_required(self):
        if self.kwargs.get("pk"):
            return (self.provider_type.get_permission("change"),)
        return (self.provider_type.get_permission("add"),)

    def _resolve_subtype(self):
        instance = self._get_instance()
        subtype_enum = self.provider_type.subtype
        if instance:
            return _lookup_subtype_by_slug(subtype_enum, instance.type)
        slug = self.kwargs.get("subtype")
        try:
            subtype = _lookup_subtype_by_slug(subtype_enum, slug)
        except KeyError as exc:
            raise Http404(f"Unknown subtype: {slug}") from exc
        if subtype not in get_available_subtypes(self.provider_type, self.request):
            raise Http404("Subtype is not enabled")
        return subtype

    def _get_instance(self):
        if not self.kwargs.get("pk"):
            return None
        return get_object_or_404(self.provider_type.model, team=self.request.team, pk=self.kwargs["pk"])

    def _template(self):
        if self.provider_type == ServiceProvider.llm:
            return "service_providers/llm_provider_form.html"
        return self.template_name

    def get(self, request, *args, **kwargs):
        subtype = self._resolve_subtype()
        instance = self._get_instance()
        primary_form, config_form = get_service_provider_forms(
            self.provider_type, team=request.team, subtype=subtype, instance=instance
        )
        return render(request, self._template(), self._get_context(primary_form, config_form, subtype, instance))

    def post(self, request, *args, **kwargs):
        subtype = self._resolve_subtype()
        instance = self._get_instance()
        primary_form, config_form = get_service_provider_forms(
            self.provider_type, team=request.team, subtype=subtype, data=request.POST, instance=instance
        )

        file_formset = None
        if request.FILES:
            file_formset = get_file_formset(request, formset_cls=config_form.file_formset_form)

        # Call is_valid() on every form before combining to avoid short-circuiting
        # away from populating the later forms' errors.
        primary_valid = primary_form.is_valid()
        config_valid = config_form.is_valid()
        file_formset_valid = not file_formset or file_formset.is_valid()
        if primary_valid and config_valid and file_formset_valid:
            with transaction.atomic():
                obj = primary_form.save(commit=False)
                obj.team = request.team
                config_form.save(obj)
                obj.save()
                if file_formset:
                    files = file_formset.save(request)
                    obj.add_files(files)
                if isinstance(obj, VoiceProvider):
                    for warning in obj.run_post_save_hook():
                        messages.warning(request, warning)
            return HttpResponseRedirect(self.get_success_url())

        if file_formset and not file_formset.is_valid():
            messages.error(request, ", ".join(file_formset.non_form_errors()))
        return render(request, self._template(), self._get_context(primary_form, config_form, subtype, instance))

    def _get_context(self, primary_form, config_form, subtype, instance):
        ctx = {
            "primary_form": primary_form,
            "config_form": config_form,
            "provider": self.provider_type,
            "subtype": subtype,
            "object": instance,
            "title": f"Edit {instance.name}" if instance else self.provider_type.label,
            "button_text": "Update" if instance else "Create",
            "active_tab": "manage-team",
        }
        is_elevenlabs_voice = (
            isinstance(instance, VoiceProvider) and instance.type == VoiceProviderType.elevenlabs.value
        )
        if is_elevenlabs_voice:
            ctx["sync_voices_url"] = reverse(
                "service_providers:sync_voices",
                kwargs={
                    "team_slug": self.request.team.slug,
                    "provider_type": "voice",
                    "pk": instance.pk,
                },
            )
        if self.provider_type == ServiceProvider.llm:
            default_llm_models_by_type = _get_models_by_type(LlmProviderModel.objects.filter(team=None))
            embedding_models_by_type = _get_models_by_type(EmbeddingProviderModel.objects.filter(team=None))
            custom_llm_models_by_type = _get_models_by_type(LlmProviderModel.objects.filter(team=self.request.team))
            ctx.update(
                {
                    "default_llm_models_by_type": default_llm_models_by_type,
                    "custom_llm_models_by_type": custom_llm_models_by_type,
                    "embedding_models_by_type": embedding_models_by_type,
                    "new_model_form": LlmProviderModelForm(self.request.team),
                }
            )
        return ctx

    def get_success_url(self):
        return resolve_url("single_team:manage_team", team_slug=self.request.team.slug)


def _get_models_by_type(queryset):
    models_by_type = defaultdict(list)
    for model in queryset:
        models_by_type[model.type].append(model)
    return {key: sorted(value, key=lambda x: x.name) for key, value in models_by_type.items()}


@require_POST
@login_and_team_required
@permission_required("service_providers.add_llmprovidermodel")
def create_llm_provider_model(request, team_slug: str):
    form = LlmProviderModelForm(request.team, request.POST)
    if form.is_valid():
        model = form.save(commit=False)
        model.team = request.team
        model.save()
    else:
        if len(form.errors) == 1 and "__all__" in form.errors:
            return HttpResponseBadRequest(", ".join([str(v) for v in form.errors.values()]))
        return HttpResponseBadRequest(str(form.errors))
    return render(
        request,
        "service_providers/components/custom_llm_models.html",
        {
            "llm_models_by_type": _get_models_by_type(LlmProviderModel.objects.filter(team=request.team)),
            "embedding_models_by_type": _get_models_by_type(LlmProviderModel.objects.filter(team=request.team)),
            "for_type": form.cleaned_data["type"],
        },
    )


@require_http_methods(["DELETE"])
@login_required
@permission_required("service_providers.delete_llmprovidermodel")
def delete_llm_provider_model(request, team_slug: str, pk: int):
    llm_provider_model = get_object_or_404(LlmProviderModel, team=request.team, pk=pk)
    try:
        llm_provider_model.delete()
    except ValidationError as ex:
        return HttpResponseBadRequest(", ".join(ex.messages).encode("utf-8"))
    return HttpResponse()


@require_POST
@login_and_team_required
@permission_required("service_providers.change_voiceprovider", raise_exception=True)
def sync_voices(request, team_slug: str, provider_type: str, pk: int):
    provider = get_object_or_404(VoiceProvider, team=request.team, pk=pk)
    try:
        provider.sync_voices()
        count = provider.syntheticvoice_set.count()
        messages.success(request, f"Voices synced successfully. {count} voice(s) available.")
    except Exception:
        log.exception("Failed to sync voices for provider %s", pk)
        messages.error(request, "Voice sync failed. Please check your API key and try again.")
    return redirect("service_providers:edit", team_slug=team_slug, provider_type=provider_type, pk=pk)
