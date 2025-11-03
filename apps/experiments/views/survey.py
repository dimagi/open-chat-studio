from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.experiments.forms import SurveyForm
from apps.experiments.models import Survey
from apps.experiments.tables import SurveyTable
from apps.generics.help import render_help_with_link
from apps.teams.mixins import LoginAndTeamRequiredMixin


class SurveyHome(LoginAndTeamRequiredMixin, TemplateView):
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "survey",
            "title": "Survey",
            "title_help_content": render_help_with_link("", "survey"),
            "new_object_url": reverse("experiments:survey_new", args=[team_slug]),
            "table_url": reverse("experiments:survey_table", args=[team_slug]),
        }


class SurveyTableView(SingleTableView):
    model = Survey
    table_class = SurveyTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return Survey.objects.filter(team=self.request.team, is_version=False)


class CreateSurvey(CreateView):
    model = Survey
    form_class = SurveyForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Survey",
        "button_text": "Create",
        "active_tab": "survey",
    }

    def get_success_url(self):
        return reverse("experiments:survey_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.owner = self.request.user
        return super().form_valid(form)


class EditSurvey(UpdateView):
    model = Survey
    form_class = SurveyForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update Survey",
        "button_text": "Update",
        "active_tab": "survey",
    }

    def get_queryset(self):
        return Survey.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("experiments:survey_home", args=[self.request.team.slug])


class DeleteSurvey(LoginAndTeamRequiredMixin, View):
    def delete(self, request, team_slug: str, pk: int):
        survey = get_object_or_404(Survey, id=pk, team=request.team)
        survey.archive()
        messages.success(request, "Survey Deleted")
        return HttpResponse()
