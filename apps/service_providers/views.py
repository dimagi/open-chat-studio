from collections import defaultdict

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render, resolve_url
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from django.views.generic import TemplateView
from django.views.generic.edit import ModelFormMixin, SingleObjectMixin
from django_tables2 import SingleTableView
from waffle import flag_is_active

from apps.files.views import BaseAddFileHtmxView
from apps.service_providers.forms import LlmProviderModelForm, LlmProviderModelForm2
from apps.service_providers.models import LlmProviderModel, MessagingProviderType, VoiceProviderType
from apps.service_providers.tables import LlmProviderModelTable

from ..generics.views import BaseTypeSelectFormView
from ..teams.decorators import login_and_team_required
from .utils import ServiceProvider, get_service_provider_config_form


class ServiceProviderMixin:
    @property
    def provider_type(self) -> ServiceProvider:
        type_ = self.kwargs["provider_type"]
        return ServiceProvider[type_]


class ServiceProviderTableView(SingleTableView, ServiceProviderMixin):
    paginate_by = 25
    template_name = "table/single_table.html"

    def get_queryset(self):
        return self.provider_type.model.objects.filter(team=self.request.team)

    def get_table_class(self):
        return self.provider_type.table


@require_http_methods(["DELETE"])
def delete_service_provider(request, team_slug: str, provider_type: str, pk: int):
    provider = ServiceProvider[provider_type]
    service_config = get_object_or_404(provider.model, team=request.team, pk=pk)
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


class CreateServiceProvider(BaseTypeSelectFormView, ServiceProviderMixin):
    @property
    def extra_context(self):
        return {"active_tab": "manage-team", "title": self.provider_type.label}

    @property
    def model(self):
        return self.provider_type.model

    def get_form(self, data=None):
        forms_to_exclude = []
        if not flag_is_active(self.request, "open_ai_voice_engine"):
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


class LlmProviderModelTableView(PermissionRequiredMixin, SingleTableView):
    permission_required = "service_providers.view_llmprovidermodel"
    paginate_by = 25
    template_name = "table/single_table.html"
    model = LlmProviderModel
    table_class = LlmProviderModelTable

    def get_queryset(self):
        return LlmProviderModel.objects.filter(team=self.request.team)


class LlmProviderModelView(PermissionRequiredMixin, ModelFormMixin, SingleObjectMixin, TemplateView):
    permission_required = ("service_providers.add_llmprovidermodel", "service_providers.change_llmprovidermodel")
    model = LlmProviderModel
    form_class = LlmProviderModelForm
    template_name = "generic/object_form.html"

    def get_form_kwargs(self):
        return {"team": self.request.team, **super().get_form_kwargs()}

    @property
    def extra_context(self):
        return {
            "title": self._get_title(),
            "button_text": "Save",
        }

    def _get_title(self):
        if self.object:
            return "Edit Custom LLM Model"
        return "Create Custom LLM Model"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.extra_context)
        return context

    def get(self, request, *args, **kwargs):
        if "pk" in self.kwargs:
            self.object = self.get_object()
        else:
            self.object = None
        return self.render_to_response(self.get_context_data(form=self.get_form()))

    def post(self, request, *args, **kwargs):
        if "pk" in self.kwargs:
            self.object = self.get_object()
        else:
            self.object = None
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        else:
            return self.form_invalid(form)

    def get_success_url(self):
        return resolve_url("single_team:manage_team", team_slug=self.request.team.slug)

    def form_valid(self, form):
        if not self.object:
            form.instance.team = self.request.team
        return super().form_valid(form)

    def get_queryset(self):
        return LlmProviderModel.objects.filter(team=self.request.team)


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


class LlmProviderView(CreateServiceProvider):
    template = "service_providers/llm_provider_form.html"

    @property
    def provider_type(self) -> ServiceProvider:
        return ServiceProvider.llm

    @property
    def extra_context(self):
        default_models_by_type = _get_models_by_type(LlmProviderModel.objects.filter(team=None))
        custom_models_type_type = _get_models_by_type(LlmProviderModel.objects.filter(team=self.request.team))
        return {
            "active_tab": "manage-team",
            "title": self.provider_type.label,
            "default_models_by_type": default_models_by_type,
            "custom_models_by_type": custom_models_type_type,
            "new_model_form": LlmProviderModelForm2(),
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
    form = LlmProviderModelForm2(request.POST)
    if form.is_valid():
        model = form.save(commit=False)
        model.team = request.team
        model.save()
    return render(
        request,
        "service_providers/components/custom_llm_models.html",
        {"models_by_type": _get_models_by_type(LlmProviderModel.objects.filter(team=request.team))},
    )
