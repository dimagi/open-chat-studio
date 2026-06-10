from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.experiments.forms import SurveyForm
from apps.experiments.models import Survey
from apps.experiments.tables import SurveyTable
from apps.generics.help import render_help_with_link
from apps.teams.mixins import LoginAndTeamRequiredMixin

SURVEY_DEPRECATION_MESSAGE = (
    "Surveys are deprecated and will be removed on 2026-07-10. New surveys can no longer be created."
)


class SurveyHome(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "experiments/survey_home.html"
    permission_required = "experiments.view_survey"

    def get_context_data(self, team_slug: str, **kwargs):  # ty: ignore[invalid-method-override]
        return {
            "active_tab": "survey",
            "title": "Survey",
            "title_help_content": render_help_with_link("", "survey"),
            "allow_new": False,
            "table_url": reverse("experiments:survey_table", args=[team_slug]),
        }


class SurveyTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):
    model = Survey
    table_class = SurveyTable
    template_name = "table/single_table.html"
    permission_required = "experiments.view_survey"

    def get_queryset(self):
        return Survey.objects.filter(team=self.request.team, is_version=False)


class CreateSurvey(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    """Survey creation is disabled during deprecation."""

    # view_survey (not add_survey) so any team member who lands here gets the
    # informative deprecation redirect rather than a 403.
    permission_required = "experiments.view_survey"

    def dispatch(self, request, team_slug: str, *args, **kwargs):
        messages.error(request, SURVEY_DEPRECATION_MESSAGE)
        return HttpResponseRedirect(reverse("experiments:survey_home", args=[team_slug]))


class EditSurvey(LoginAndTeamRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Survey
    form_class = SurveyForm
    template_name = "experiments/survey_form.html"
    extra_context = {
        "title": "View Survey",
        "page_title": "View Survey",
        "active_tab": "survey",
    }
    permission_required = "experiments.view_survey"

    def get_queryset(self):
        return Survey.objects.filter(team=self.request.team)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        for field in form.fields.values():
            field.disabled = True
        return form

    def post(self, request, *args, **kwargs):
        # Reject all edits without loading self.object; this relies on the
        # self-contained get_success_url() below (which doesn't touch self.object).
        messages.error(request, "Surveys are read-only and can no longer be edited.")
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse("experiments:survey_home", args=[self.request.team.slug])


class DeleteSurvey(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "experiments.delete_survey"

    def delete(self, request, team_slug: str, pk: int):
        survey = get_object_or_404(Survey, id=pk, team=request.team)
        survey.archive()
        messages.success(request, "Survey Deleted")
        return HttpResponse()
