from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import CreateView, UpdateView
from django_tables2 import SingleTableView

from apps.analysis.forms import AnalysisForm
from apps.analysis.models import Analysis, AnalysisRun
from apps.analysis.pipelines import get_param_forms
from apps.analysis.tables import AnalysisRunTable, AnalysisTable
from apps.analysis.tasks import run_pipeline
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


@login_and_team_required
def analysis_details(request, team_slug: str, pk: int):
    analysis = get_object_or_404(Analysis, id=pk, team=request.team)
    return TemplateResponse(
        request,
        "analysis/analysis_details.html",
        {
            "analysis": analysis,
        },
    )


class AnalysisRunTableView(SingleTableView):
    model = AnalysisRun
    paginate_by = 25
    table_class = AnalysisRunTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return AnalysisRun.objects.filter(team=self.request.team, analysis=self.kwargs["pk"])


class AnalysisTableView(SingleTableView):
    model = Analysis
    paginate_by = 25
    table_class = AnalysisTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return Analysis.objects.filter(team=self.request.team)


class CreateAnalysisPipeline(CreateView):
    model = Analysis
    form_class = AnalysisForm
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
    form_class = AnalysisForm
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


@login_and_team_required
def create_analysis_run(request, team_slug: str, pk: int):
    analysis = get_object_or_404(Analysis, id=pk, team=request.team)
    param_forms = get_param_forms(analysis.source)
    if request.method == "POST":
        forms = {
            step_name: form(request, data=request.POST, files=request.FILES) for step_name, form in param_forms.items()
        }
        if all(form.is_valid() for form in forms.values()):
            step_params = {step_name: form.save().model_dump() for step_name, form in forms.items()}
            run = AnalysisRun.objects.create(
                team=analysis.team,
                analysis=analysis,
                params=step_params,
            )
            result = run_pipeline.delay(run.id)
            run.task_id = result.task_id
            run.save()
            return redirect("analysis:run_details", team_slug=team_slug, pk=run.id)
    else:
        forms = {step_name: form(request) for step_name, form in param_forms.items()}
    return render(
        request,
        "analysis/analysis_run_create.html",
        {
            "analysis": analysis,
            "param_forms": forms,
        },
    )


@login_and_team_required
def run_details(request, team_slug: str, pk: int):
    run = get_object_or_404(AnalysisRun, id=pk, team=request.team)
    return render(
        request,
        "analysis/run_details.html",
        {"run": run},
    )


@login_and_team_required
def run_progress(request, team_slug: str, pk: int):
    run = get_object_or_404(AnalysisRun, id=pk, team=request.team)
    if not run.is_complete and run.task_id:
        return render(
            request,
            "analysis/components/run_progress.html",
            {"run": run, "update_status": True},
        )
    else:
        return render(
            request,
            "analysis/components/run_detail_tabs.html",
            {"run": run, "update_status": True},
        )
