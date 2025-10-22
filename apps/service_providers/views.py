from collections import defaultdict

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render, resolve_url
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from django_htmx.http import reswap
from django_tables2 import SingleTableView
from waffle import flag_is_active

from apps.assistants.models import OpenAiAssistant
from apps.experiments.models import Experiment
from apps.files.views import BaseAddFileHtmxView
from apps.service_providers.forms import LlmProviderModelForm
from apps.service_providers.models import (
    EmbeddingProviderModel,
    LlmProviderModel,
    MessagingProviderType,
    VoiceProviderType,
)
from apps.utils.deletion import get_related_objects

from ..generics.chips import Chip
from ..generics.views import BaseTypeSelectFormView
from ..teams.decorators import login_and_team_required
from ..teams.mixins import LoginAndTeamRequiredMixin
from .utils import ServiceProvider, get_service_provider_config_form


class ServiceProviderMixin:
    @property
    def provider_type(self) -> ServiceProvider:
        type_ = self.kwargs["provider_type"]
        return ServiceProvider[type_]


class ServiceProviderTableView(
    LoginAndTeamRequiredMixin, SingleTableView, ServiceProviderMixin, PermissionRequiredMixin
):
    paginate_by = 25
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
        response = render_to_string(
            "generic/referenced_objects.html",
            context={
                "object_name": "service provider",
                "experiments": related_experiments,
                "assistants": related_assistants,
            },
        )
        return reswap(HttpResponse(response, status=400), "none")
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
    LoginAndTeamRequiredMixin, BaseTypeSelectFormView, ServiceProviderMixin, PermissionRequiredMixin
):
    def get_permission_required(self):
        if self.kwargs.get("pk"):
            return (self.provider_type.get_permission("change"),)
        return (self.provider_type.get_permission("add"),)

    @property
    def extra_context(self):
        return {"active_tab": "manage-team", "title": self.provider_type.label}

    @property
    def model(self):
        return self.provider_type.model

    def get_form(self, data=None):
        forms_to_exclude = []
        if not flag_is_active(self.request, "flag_open_ai_voice_engine"):
            forms_to_exclude.append(VoiceProviderType.openai_voice_engine)

        if not settings.SLACK_ENABLED:
            forms_to_exclude.append(MessagingProviderType.slack)

        return get_service_provider_config_form(
            self.provider_type,
            team=self.request.team,
            data=data,
            instance=self.get_object(),
            exclude_forms=forms_to_exclude,
        )

    @transaction.atomic()
    def form_valid(self, form, file_formset):
        instance = form.save()
        instance.team = self.request.team
        instance.save()
        if file_formset:
            files = file_formset.save(self.request)
            instance.add_files(files)

    def get_success_url(self):
        return resolve_url("single_team:manage_team", team_slug=self.request.team.slug)


class LlmProviderView(CreateServiceProvider):
    template = "service_providers/llm_provider_form.html"

    @property
    def provider_type(self) -> ServiceProvider:
        return ServiceProvider.llm

    @property
    def extra_context(self):
        default_llm_models_by_type = _get_models_by_type(LlmProviderModel.objects.filter(team=None))
        embedding_models_by_type = _get_models_by_type(EmbeddingProviderModel.objects.filter(team=None))
        custom_llm_models_type_type = _get_models_by_type(LlmProviderModel.objects.filter(team=self.request.team))
        return {
            "active_tab": "manage-team",
            "title": self.provider_type.label,
            "default_llm_models_by_type": default_llm_models_by_type,
            "custom_llm_models_by_type": custom_llm_models_type_type,
            "embedding_models_by_type": embedding_models_by_type,
            "new_model_form": LlmProviderModelForm(self.request.team),
        }


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
