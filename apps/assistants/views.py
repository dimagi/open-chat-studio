from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.service_providers.utils import get_llm_provider_choices
from apps.teams.mixins import LoginAndTeamRequiredMixin

from .forms import OpenAiAssistantForm
from .models import OpenAiAssistant
from .tables import OpenAiAssistantTable


class OpenAiAssistantHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "assistants",
            "title": "OpenAI Assistants",
            "new_object_url": reverse("assistants:new", args=[team_slug]),
            "table_url": reverse("assistants:table", args=[team_slug]),
        }


class OpenAiAssistantTableView(SingleTableView):
    paginate_by = 25
    template_name = "table/single_table.html"
    table_class = OpenAiAssistantTable

    def get_queryset(self):
        return OpenAiAssistant.objects.filter(team=self.request.team)


class BaseOpenAiAssistantView(LoginAndTeamRequiredMixin):
    model = OpenAiAssistant
    template_name = "assistants/assistant_form.html"
    form_class = OpenAiAssistantForm
    title = ""
    button_text = ""

    @property
    def extra_context(self):
        return {
            "title": self.title,
            "button_text": self.button_text,
            "active_tab": "assistants",
            "form_attrs": {"x-data": "assistant"},
            "llm_options": get_llm_provider_choices(self.request.team),
        }

    def get_form_kwargs(self):
        return {"request": self.request, **super().get_form_kwargs()}

    def get_success_url(self):
        return reverse("assistants:home", args=[self.request.team.slug])

    def get_queryset(self):
        return OpenAiAssistant.objects.filter(team=self.request.team)


class CreateOpenAiAssistant(BaseOpenAiAssistantView, CreateView):
    title = "Create OpenAI Assistant"
    button_text = "Create"


class EditOpenAiAssistant(BaseOpenAiAssistantView, UpdateView):
    title = "Edit OpenAI Assistant"
    button_text = "Update"


class DeleteOpenAiAssistant(LoginAndTeamRequiredMixin, View):
    def delete(self, request, team_slug: str, pk: int):
        assistant = get_object_or_404(OpenAiAssistant, team=request.team, pk=pk)
        assistant.delete()
        # TODO: delete assistant from OpenAI
        return HttpResponse()
