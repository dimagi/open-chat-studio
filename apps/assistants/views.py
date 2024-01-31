from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
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
from .sync import delete_openai_assistant, push_assistant_to_openai
from .tables import OpenAiAssistantTable
from .utils import get_llm_providers_for_assistants


class OpenAiAssistantHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    template_name = "generic/object_home.html"
    permission_required = "analysis.view_openaiassistant"

    def get_context_data(self, team_slug: str, **kwargs):
        has_providers = get_llm_providers_for_assistants(self.request.team).exists()
        if not has_providers:
            messages.warning(self.request, "You need to add an OpenAI LLM provider before you can create an assistant.")
        return {
            "active_tab": "assistants",
            "title": "OpenAI Assistants",
            "new_object_url": reverse("assistants:new", args=[team_slug]),
            "table_url": reverse("assistants:table", args=[team_slug]),
            "allow_new": has_providers and self.request.user.has_perm("assistants.add_openaiassistant"),
        }


class OpenAiAssistantTableView(SingleTableView, PermissionRequiredMixin):
    paginate_by = 25
    template_name = "table/single_table.html"
    table_class = OpenAiAssistantTable
    permission_required = "analysis.view_openaiassistant"

    def get_queryset(self):
        return OpenAiAssistant.objects.filter(team=self.request.team)


class BaseOpenAiAssistantView(LoginAndTeamRequiredMixin, PermissionRequiredMixin):
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
    permission_required = "assistants.add_openaiassistant"

    @transaction.atomic()
    def form_valid(self, form):
        response = super().form_valid(form)
        push_assistant_to_openai(self.object)
        return response


class EditOpenAiAssistant(BaseOpenAiAssistantView, UpdateView):
    title = "Edit OpenAI Assistant"
    button_text = "Update"
    permission_required = "assistants.change_openaiassistant"

    @transaction.atomic()
    def form_valid(self, form):
        response = super().form_valid(form)
        push_assistant_to_openai(self.object)
        return response


class DeleteOpenAiAssistant(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "assistants.delete_openaiassistant"

    @transaction.atomic()
    def delete(self, request, team_slug: str, pk: int):
        assistant = get_object_or_404(OpenAiAssistant, team=request.team, pk=pk)
        assistant.delete()
        delete_openai_assistant(assistant)
        return HttpResponse()
