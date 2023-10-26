from django.http import HttpResponse
from django.shortcuts import get_object_or_404, resolve_url
from django_tables2 import SingleTableView

from ..generics.views import BaseTypeSelectFormView
from .models import LlmProvider
from .tables import LlmProviderTable
from .utils import get_llm_config_form


class ServiceProviderTableView(SingleTableView):
    paginate_by = 25
    template_name = "table/single_table.html"

    @property
    def provider_type(self):
        type_ = self.kwargs["provider_type"]
        if type_ not in ["llm"]:
            raise ValueError(f"Invalid provider type: {type_}")
        return type_

    def get_queryset(self):
        match self.provider_type:
            case "llm":
                return LlmProvider.objects.filter(team=self.request.team)

    def get_table_class(self):
        match self.provider_type:
            case "llm":
                return LlmProviderTable


def delete_service_provider(request, team_slug: str, provider_type: str, pk: int):
    match provider_type:
        case "llm":
            object_type = LlmProvider
        case _:
            raise ValueError(f"Invalid provider type: {provider_type}")

    service_config = get_object_or_404(object_type, team=request.team, pk=pk)
    service_config.delete()
    return HttpResponse()


class CreateServiceProvider(BaseTypeSelectFormView):
    extra_context = {
        "active_tab": "manage-team",
    }

    @property
    def provider_type(self):
        type_ = self.kwargs["provider_type"]
        if type_ not in ["llm"]:
            raise ValueError(f"Invalid provider type: {type_}")
        return type_

    @property
    def model(self):
        match self.provider_type:
            case "llm":
                return LlmProvider

    def get_form(self, data=None):
        match self.provider_type:
            case "llm":
                return get_llm_config_form(data=data, instance=self.get_object())

    def form_valid(self, form):
        instance = form.save()
        instance.team = self.request.team
        instance.save()

    def get_success_url(self):
        return resolve_url("single_team:manage_team", team_slug=self.request.team.slug)
