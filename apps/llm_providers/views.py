from django.http import HttpResponse
from django.shortcuts import get_object_or_404, resolve_url
from django_tables2 import SingleTableView

from ..generics.views import BaseCombinedForm
from .models import LlmProvider
from .tables import LlmProviderTable
from .utils import get_llm_config_form


class LlmProviderTableView(SingleTableView):
    paginate_by = 25
    table_class = LlmProviderTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return LlmProvider.objects.filter(team=self.request.team)


def delete_llm_provider(request, team_slug: str, pk: int):
    service_config = get_object_or_404(LlmProvider, team=request.team, pk=pk)
    service_config.delete()
    return HttpResponse()


class CreateEditLlmProvider(BaseCombinedForm):
    model = LlmProvider
    extra_context = {
        "active_tab": "manage-team",
    }
    title = "Create LLM Provider"

    def get_combined_form(self, data=None):
        return get_llm_config_form(data=data, instance=self.get_object())

    def form_valid(self, combined_form):
        instance = combined_form.save()
        instance.team = self.request.team
        instance.save()

    def get_success_url(self):
        return resolve_url("single_team:manage_team", team_slug=self.request.team.slug)
