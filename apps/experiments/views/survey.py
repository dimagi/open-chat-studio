from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import CreateView, UpdateView
from django_tables2 import SingleTableView

from apps.experiments.models import Survey
from apps.experiments.tables import SurveyTable
from apps.teams.decorators import login_and_team_required


@login_and_team_required
def survey_home(request, team_slug: str):
    return TemplateResponse(
        request,
        "generic/object_home.html",
        {
            "active_tab": "survey",
            "title": "Survey",
            "new_object_url": reverse("experiments:survey_new", args=[team_slug]),
            "table_url": reverse("experiments:survey_table", args=[team_slug]),
        },
    )


class SurveyTableView(SingleTableView):
    model = Survey
    paginate_by = 25
    table_class = SurveyTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return Survey.objects.filter(team=self.request.team)


class CreateSurvey(CreateView):
    model = Survey
    fields = [
        "name",
        "url",
    ]
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
    fields = [
        "name",
        "url",
    ]
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


@login_and_team_required
def delete_survey(request, team_slug: str, pk: int):
    survey = get_object_or_404(Survey, id=pk, team=request.team)
    survey.delete()
    return HttpResponse()
