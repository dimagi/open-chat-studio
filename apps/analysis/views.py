from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import CreateView, UpdateView
from django_tables2 import SingleTableView

from apps.analysis.forms import AnalysisForm
from apps.analysis.models import Analysis, Resource, RunGroup, RunStatus
from apps.analysis.pipelines import get_data_pipeline, get_param_forms, get_source_pipeline
from apps.analysis.tables import AnalysisTable, RunGroupTable
from apps.analysis.tasks import run_analysis
from apps.service_providers.utils import get_llm_provider_choices
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


class RunGroupTableView(SingleTableView):
    model = RunGroup
    paginate_by = 25
    table_class = RunGroupTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return RunGroup.objects.filter(team=self.request.team, analysis=self.kwargs["pk"])


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
    template_name = "analysis/analysis_form.html"

    @property
    def extra_context(self):
        return {
            "title": "Create Analysis Pipeline",
            "button_text": "Create",
            "active_tab": "analysis",
            "form_attrs": {"x-data": "analysis"},
            "llm_options": get_llm_provider_choices(self.request.team),
        }

    def get_form(self, form_class=None):
        return self.get_form_class()(self.request, **self.get_form_kwargs())

    def get_success_url(self):
        return reverse("analysis:home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        return super().form_valid(form)


class EditAnalysisPipeline(UpdateView):
    model = Analysis
    form_class = AnalysisForm
    template_name = "analysis/analysis_form.html"

    @property
    def extra_context(self):
        return {
            "title": "Update Analysis Pipeline",
            "button_text": "Update",
            "active_tab": "analysis",
            "form_attrs": {"x-data": "analysis"},
            "llm_options": get_llm_provider_choices(self.request.team),
        }

    def get_form(self, form_class=None):
        return self.get_form_class()(self.request, **self.get_form_kwargs())

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
def create_analysis_run(request, team_slug: str, pk: int, run_id: int = None):
    analysis = get_object_or_404(Analysis, id=pk, team=request.team)
    param_forms = {
        **get_param_forms(get_source_pipeline(analysis.source)),
        **get_param_forms(get_data_pipeline(analysis.pipeline)),
    }
    if request.method == "POST":
        forms = {
            step_name: form(request, data=request.POST, files=request.FILES) for step_name, form in param_forms.items()
        }
        if all(form.is_valid() for form in forms.values()):
            step_params = {
                step_name: form.save().model_dump(exclude_defaults=True) for step_name, form in forms.items()
            }
            group = RunGroup.objects.create(
                team=analysis.team,
                analysis=analysis,
                params=step_params,
            )
            result = run_analysis.delay(group.id)
            group.task_id = result.task_id
            group.save()
            return redirect("analysis:group_details", team_slug=team_slug, pk=group.id)
    else:
        initial = {}
        if run_id:
            run = get_object_or_404(RunGroup, id=run_id, team=request.team)
            initial = run.params
        forms = {
            step_name: form(request, initial=initial.get(step_name, {})) for step_name, form in param_forms.items()
        }
    return render(
        request,
        "analysis/analysis_run_create.html",
        {
            "analysis": analysis,
            "param_forms": forms,
        },
    )


@login_and_team_required
def replay_run(request, team_slug: str, pk: int):
    run = get_object_or_404(RunGroup, id=pk, team=request.team)
    return create_analysis_run(request, team_slug, run.analysis.id, run_id=pk)


@login_and_team_required
def run_group_details(request, team_slug: str, pk: int):
    group = get_object_or_404(RunGroup, id=pk, team=request.team)
    return render(
        request,
        "analysis/run_group_details.html",
        {"group": group, "runs": group.analysisrun_set.all()},
    )


@login_and_team_required
def group_progress(request, team_slug: str, pk: int):
    group = get_object_or_404(RunGroup, id=pk, team=request.team)
    runs = group.analysisrun_set.all()
    if not group.is_complete and group.task_id:
        return render(
            request,
            "analysis/components/group_progress.html",
            {"group": group, "update_status": True, "runs": runs},
        )
    else:
        return render(
            request,
            "analysis/components/group_detail_tabs.html",
            {"group": group, "update_status": True, "runs": runs},
        )


@login_and_team_required
def download_resource(request, team_slug: str, pk: int):
    resource = get_object_or_404(Resource, id=pk, team=request.team)
    try:
        file = resource.file.open()
        return FileResponse(file, as_attachment=True, filename=resource.file.name)
    except FileNotFoundError:
        raise Http404()
