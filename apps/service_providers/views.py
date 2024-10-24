from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, resolve_url
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django_tables2 import SingleTableView
from waffle import flag_is_active

from apps.files.views import BaseAddFileHtmxView
from apps.service_providers.models import LlmProviderModel, MessagingProviderType, VoiceProviderType
from apps.service_providers.tables import LlmProviderModelTable

from ..generics.views import BaseTypeSelectFormView
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


class LlmProviderModelTableView(SingleTableView):
    paginate_by = 25
    template_name = "table/single_table.html"
    model = LlmProviderModel
    table_class = LlmProviderModelTable

    def get_queryset(self):
        return LlmProviderModel.objects.filter(team=self.request.team)
