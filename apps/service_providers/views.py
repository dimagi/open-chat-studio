from django.http import HttpResponse
from django.shortcuts import get_object_or_404, resolve_url
from django_tables2 import SingleTableView

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


def delete_service_provider(request, team_slug: str, provider_type: str, pk: int):
    provider = ServiceProvider[provider_type]
    service_config = get_object_or_404(provider.model, team=request.team, pk=pk)
    service_config.delete()
    return HttpResponse()


class CreateServiceProvider(BaseTypeSelectFormView, ServiceProviderMixin):
    @property
    def extra_context(self):
        return {"active_tab": "manage-team", "title": self.provider_type.label}

    @property
    def model(self):
        return self.provider_type.model

    def get_form(self, data=None):
        return get_service_provider_config_form(self.provider_type, data=data, instance=self.get_object())

    def form_valid(self, form):
        instance = form.save()
        instance.team = self.request.team
        instance.save()

    def get_success_url(self):
        return resolve_url("single_team:manage_team", team_slug=self.request.team.slug)
