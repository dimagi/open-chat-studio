from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import CreateView, UpdateView
from django_tables2 import SingleTableView

from apps.analysis.models import Analysis
from apps.analysis.tables import AnalysisTable
from apps.teams.decorators import login_and_team_required


@login_and_team_required
def analysis_home(request, team_slug: str):
    return TemplateResponse(
        request,
        "generic/object_home.html",
        {
            "active_tab": "analysis",
            "title": "Analysis Pipelines",
            "new_object_url": reverse("analysis:new", args=[team_slug]),
            "table_url": reverse("analysis:table", args=[team_slug]),
        },
    )


class AnalysisTableView(SingleTableView):
    model = Analysis
    paginate_by = 25
    table_class = AnalysisTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return Analysis.objects.filter(team=self.request.team)


class CreateAnalysisPipeline(CreateView):
    model = Analysis
    fields = [
        "name",
        "source",
        "pipelines",
        "llm_provider",
    ]
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Analysis Pipeline",
        "button_text": "Create",
        "active_tab": "analysis",
    }

    def get_success_url(self):
        return reverse("analysis:home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        return super().form_valid(form)


class EditAnalysisPipeline(UpdateView):
    model = Analysis
    fields = [
        "name",
        "source",
        "pipelines",
        "llm_provider",
    ]
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update Analysis Pipeline",
        "button_text": "Update",
        "active_tab": "analysis",
    }

    def get_queryset(self):
        return Analysis.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("analysis:home", args=[self.request.team.slug])


@login_and_team_required
def delete_analysis(request, team_slug: str, pk: int):
    prompt = get_object_or_404(Analysis, id=pk, team=request.team)
    prompt.delete()
    return HttpResponse()
