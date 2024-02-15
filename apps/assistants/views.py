from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, FormView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.service_providers.utils import get_llm_provider_choices
from apps.teams.mixins import LoginAndTeamRequiredMixin

from ..files.forms import get_file_formset
from ..files.views import BaseAddFileHtmxView, BaseDeleteFileView
from ..generics import actions
from ..service_providers.models import LlmProvider
from ..utils.tables import render_table_row
from .forms import ImportAssistantForm, OpenAiAssistantForm
from .models import OpenAiAssistant
from .sync import (
    OpenAiSyncError,
    delete_file_from_openai,
    delete_openai_assistant,
    import_openai_assistant,
    push_assistant_to_openai,
    sync_from_openai,
)
from .tables import OpenAiAssistantTable
from .utils import get_llm_providers_for_assistants


class OpenAiAssistantHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    template_name = "generic/object_home.html"
    permission_required = "assistants.view_openaiassistant"

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
            "button_style": "btn-primary",
            "actions": [
                actions.Action(
                    "assistants:import",
                    label="Import",
                    icon_class="fa-solid fa-file-import",
                    required_permissions=["assistants.add_openaiassistant"],
                )
            ],
        }


class OpenAiAssistantTableView(SingleTableView, PermissionRequiredMixin):
    paginate_by = 25
    template_name = "table/single_table.html"
    table_class = OpenAiAssistantTable
    permission_required = "assistants.view_openaiassistant"

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
            "form_attrs": {"x-data": "assistant", "enctype": "multipart/form-data"},
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "file_formset" not in context:
            context["file_formset"] = self._get_file_formset()
        return context

    def _get_file_formset(self):
        return get_file_formset(self.request)

    def post(self, request, *args, **kwargs):
        self.object = None
        form = self.get_form()
        file_formset = self._get_file_formset()
        if form.is_valid() and file_formset.is_valid():
            return self.form_valid(form, file_formset)
        else:
            return self.form_invalid(form, file_formset)

    @transaction.atomic()
    def form_valid(self, form, file_formset):
        self.object = form.save()
        files = file_formset.save(self.request)
        self.object.files.set(files)
        try:
            push_assistant_to_openai(self.object)
        except OpenAiSyncError as e:
            messages.error(self.request, f"Error syncing assistant to OpenAI: {e}")
            return self.form_invalid(form, file_formset)
        return HttpResponseRedirect(self.get_success_url())

    def form_invalid(self, form, file_formset):
        return self.render_to_response(self.get_context_data(form=form, file_formset=file_formset))


class EditOpenAiAssistant(BaseOpenAiAssistantView, UpdateView):
    title = "Edit OpenAI Assistant"
    button_text = "Update"
    permission_required = "assistants.change_openaiassistant"

    @transaction.atomic()
    def form_valid(self, form):
        response = super().form_valid(form)
        try:
            push_assistant_to_openai(self.object)
        except OpenAiSyncError as e:
            messages.error(self.request, f"Error syncing changes to OpenAI: {e}")
            form.add_error(None, str(e))
            return self.form_invalid(form)
        return response


class DeleteOpenAiAssistant(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "assistants.delete_openaiassistant"

    @transaction.atomic()
    def delete(self, request, team_slug: str, pk: int):
        assistant = get_object_or_404(OpenAiAssistant, team=request.team, pk=pk)
        try:
            delete_openai_assistant(assistant)
        except OpenAiSyncError as e:
            messages.error(request, f"Error deleting assistant from OpenAI: {e}")
            return HttpResponse(status=500)
        assistant.delete()
        messages.success(request, "Assistant Deleted")
        return HttpResponse()


class LocalDeleteOpenAiAssistant(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "assistants.delete_openaiassistant"

    @transaction.atomic()
    def delete(self, request, team_slug: str, pk: int):
        assistant = get_object_or_404(OpenAiAssistant, team=request.team, pk=pk)
        assistant.delete()
        messages.success(request, "Assistant Deleted")
        return HttpResponse()


class SyncOpenAiAssistant(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "assistants.change_openaiassistant"

    def post(self, request, team_slug: str, pk: int):
        assistant = get_object_or_404(OpenAiAssistant, team=request.team, pk=pk)
        try:
            sync_from_openai(assistant)
        except OpenAiSyncError as e:
            messages.error(request, f"Error syncing assistant: {e}")
        return render_table_row(request, OpenAiAssistantTable, assistant)


class ImportAssistant(LoginAndTeamRequiredMixin, FormView, PermissionRequiredMixin):
    template_name = "generic/object_form.html"
    permission_required = "assistants.add_openaiassistant"
    form_class = ImportAssistantForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return {
            **context,
            "active_tab": "assistants",
            "title": "Import OpenAI Assistant",
            "button_text": "Import",
        }

    def get_form(self, form_class=None):
        return self.form_class(self.request, **self.get_form_kwargs())

    def get_success_url(self):
        return reverse("assistants:home", args=[self.request.team.slug])

    def form_valid(self, form):
        llm_provider = get_object_or_404(LlmProvider, team=self.request.team, pk=form.cleaned_data["llm_provider"])
        try:
            import_openai_assistant(form.cleaned_data["assistant_id"], llm_provider, self.request.team)
        except OpenAiSyncError as e:
            messages.error(self.request, f"Error importing assistant: {e}")
            return self.form_invalid(form)
        return super().form_valid(form)


class AddFileToAssistant(BaseAddFileHtmxView):
    @transaction.atomic()
    def form_valid(self, form):
        assistant = get_object_or_404(OpenAiAssistant, team=self.request.team, pk=self.kwargs["pk"])
        file = super().form_valid(form)
        assistant.files.add(file)
        push_assistant_to_openai(assistant)
        return file

    def get_delete_url(self, file):
        return reverse("assistants:remove_file", args=[self.request.team.slug, self.kwargs["pk"], file.pk])


class DeleteFileFromAssistant(BaseDeleteFileView):
    def get_success_response(self, file):
        assistant = get_object_or_404(OpenAiAssistant, team=self.request.team, pk=self.kwargs["pk"])
        delete_file_from_openai(assistant, file)
        return HttpResponse()
