from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, UpdateView
from django_tables2 import SingleTableView

from apps.analysis.forms import AnalysisForm
from apps.analysis.models import Analysis, Resource, RunGroup, RunStatus
from apps.analysis.pipelines import get_dynamic_forms_for_analysis, get_static_forms_for_analysis
from apps.analysis.tables import AnalysisTable, RunGroupTable
from apps.analysis.tasks import run_analysis
from apps.analysis.utils import merge_raw_params
from apps.service_providers.utils import get_llm_provider_choices
from apps.teams.decorators import login_and_team_required


@login_and_team_required
@permission_required("analysis.view_analysis")
def analysis_home(request, team_slug: str):
    return TemplateResponse(
        request,
        "generic/object_home.html",
        {
            "active_tab": "analysis",
            "title": "Analysis Pipelines",
            "new_object_url": reverse("analysis:new", args=[team_slug]),
            "table_url": reverse("analysis:table", args=[team_slug]),
            "allow_new": request.user.has_perm("analysis.add_analysis"),
        },
    )


@login_and_team_required
@permission_required("analysis.view_analysis")
def analysis_details(request, team_slug: str, pk: int):
    analysis = get_object_or_404(Analysis, id=pk, team=request.team)
    if analysis.needs_configuration():
        return redirect("analysis:configure", team_slug=team_slug, pk=pk)
    return TemplateResponse(
        request,
        "analysis/analysis_details.html",
        {
            "analysis": analysis,
        },
    )


@login_and_team_required
@permission_required("analysis.change_analysis", raise_exception=True)
def analysis_configure(request, team_slug: str, pk: int):
    analysis = get_object_or_404(Analysis, id=pk, team=request.team)
    param_forms = get_static_forms_for_analysis(analysis)
    if request.method == "POST":
        forms = {
            step_name: form(request, data=request.POST, files=request.FILES) for step_name, form in param_forms.items()
        }
        if all(form.is_valid() for form in forms.values()):
            step_params = {
                step_name: form.save().model_dump(exclude_defaults=True) for step_name, form in forms.items()
            }
            analysis.config = step_params
            analysis.save()
            return redirect("analysis:details", team_slug=team_slug, pk=pk)
    else:
        initial = analysis.config or {}
        forms = {
            step_name: form(request, initial=initial.get(step_name, {})) for step_name, form in param_forms.items()
        }

    return render(
        request,
        "analysis/analysis_configure.html",
        {
            "analysis": analysis,
            "param_forms": forms,
        },
    )


class RunGroupTableView(SingleTableView, PermissionRequiredMixin):
    model = RunGroup
    paginate_by = 25
    table_class = RunGroupTable
    template_name = "table/single_table.html"
    permission_required = "analysis.view_analysis"

    def get_queryset(self):
        return RunGroup.objects.filter(team=self.request.team, analysis=self.kwargs["pk"]).order_by("-created_at")


class AnalysisTableView(SingleTableView, PermissionRequiredMixin):
    model = Analysis
    paginate_by = 25
    table_class = AnalysisTable
    template_name = "table/single_table.html"
    permission_required = "analysis.view_analysis"

    def get_queryset(self):
        return Analysis.objects.filter(team=self.request.team)


class CreateAnalysisPipeline(CreateView, PermissionRequiredMixin):
    model = Analysis
    form_class = AnalysisForm
    template_name = "analysis/analysis_form.html"
    permission_required = "analysis.add_analysis"

    @property
    def extra_context(self):
        return {
            "title": "Create Analysis Pipeline",
            "button_text": "Continue",
            "active_tab": "analysis",
            "form_attrs": {"x-data": "analysis"},
            "llm_options": get_llm_provider_choices(self.request.team),
        }

    def get_form(self, form_class=None):
        return self.get_form_class()(self.request, **self.get_form_kwargs())

    def get_success_url(self):
        slug = self.request.team.slug
        return reverse("analysis:configure", args=[slug, self.object.id])

    def form_valid(self, form):
        form.instance.team = self.request.team
        return super().form_valid(form)


class EditAnalysisPipeline(UpdateView, PermissionRequiredMixin):
    model = Analysis
    form_class = AnalysisForm
    template_name = "analysis/analysis_form.html"
    permission_required = "analysis.change_analysis"

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
        if self.request.POST.get("configure"):
            return reverse("analysis:configure", args=[self.request.team.slug, self.object.id])
        return reverse("analysis:home", args=[self.request.team.slug])


@login_and_team_required
@permission_required("analysis.delete_analysis")
def delete_analysis(request, team_slug: str, pk: int):
    prompt = get_object_or_404(Analysis, id=pk, team=request.team)
    prompt.delete()
    messages.success(request, "Pipeline Deleted")
    if request.headers.get("HX-Request"):
        return HttpResponse()
    else:
        return redirect("analysis:home", team_slug=team_slug)


@login_and_team_required
@permission_required("analysis.add_rungroup")
def create_analysis_run(request, team_slug: str, pk: int, run_id: int = None):
    analysis = get_object_or_404(Analysis, id=pk, team=request.team)
    param_forms = get_dynamic_forms_for_analysis(analysis)
    initial = analysis.config or {}
    if request.method == "POST":
        forms = {
            step_name: form(request, data=request.POST, files=request.FILES, initial=initial.get(step_name, {}))
            for step_name, form in param_forms.items()
        }
        if all(form.is_valid() for form in forms.values()):
            step_params = {
                step_name: {**analysis.config.get(step_name, {}), **form.save().model_dump(exclude_defaults=True)}
                for step_name, form in forms.items()
            }
            group = RunGroup.objects.create(
                team=analysis.team,
                analysis=analysis,
                created_by=request.user,
                params=step_params,
            )
            result = run_analysis.delay(group.id)
            group.task_id = result.task_id
            group.save()
            return redirect("analysis:group_details", team_slug=team_slug, pk=group.id)
    else:
        if run_id:
            run = get_object_or_404(RunGroup, id=run_id, team=request.team)
            initial = merge_raw_params(initial, run.params)
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
@permission_required("analysis.add_rungroup")
def replay_run(request, team_slug: str, pk: int):
    run = get_object_or_404(RunGroup, id=pk, team=request.team)
    return create_analysis_run(request, team_slug, run.analysis.id, run_id=pk)


@login_and_team_required
@permission_required("analysis.view_rungroup")
def run_group_details(request, team_slug: str, pk: int):
    group = get_object_or_404(RunGroup, id=pk, team=request.team)
    runs = list(group.analysisrun_set.all())
    return render(
        request,
        "analysis/run_group_details.html",
        {"group": group, "runs": runs},
    )


@require_POST
@login_and_team_required
@permission_required("analysis.change_rungroup")
def group_feedback(request, team_slug: str, pk: int):
    group = get_object_or_404(RunGroup, id=pk, team=request.team)
    if request.POST.get("action") == "approve_reset":
        group.approved = None
    if request.POST.get("action") == "approve":
        group.approved = True
    elif request.POST.get("action") == "reject":
        group.approved = False
    elif request.POST.get("action") == "star":
        group.starred = True
    elif request.POST.get("action") == "unstar":
        group.starred = False
    elif request.POST.get("action") == "note":
        group.notes = request.POST.get("notes")
    group.save()
    return render(
        request,
        "analysis/components/group_feedback.html",
        {"record": group, "for_details": request.GET.get("details") == "true"},
    )


@login_and_team_required
@permission_required("analysis.view_rungroup")
def group_progress(request, team_slug: str, pk: int):
    group = get_object_or_404(RunGroup, id=pk, team=request.team)
    if request.method == "POST" and request.POST.get("action") == "cancel":
        group.status = RunStatus.CANCELLING
        group.save()
        group.analysisrun_set.filter(status__in=(RunStatus.PENDING, RunStatus.RUNNING)).update(
            status=RunStatus.CANCELLED
        )

    if not group.is_complete and group.task_id:
        runs = group.analysisrun_set.all()
        return render(
            request,
            "analysis/components/group_progress_inner.html",
            {"group": group, "update_status": True, "runs": runs},
        )
    else:
        return HttpResponse(headers={"HX-Redirect": reverse("analysis:group_details", args=[team_slug, pk])})


@login_and_team_required
@permission_required("analysis.view_resource")
def download_resource(request, team_slug: str, pk: int):
    resource = get_object_or_404(Resource, id=pk, team=request.team)
    try:
        file = resource.file.open()
        return FileResponse(file, as_attachment=True, filename=resource.file.name)
    except FileNotFoundError:
        raise Http404()


@login_and_team_required
@permission_required("analysis.delete_rungroup")
def delete_run_group(request, team_slug: str, pk: int):
    group = get_object_or_404(RunGroup, id=pk, team=request.team)
    group.delete()
    messages.success(request, "Run Deleted")
    if request.headers.get("HX-Request"):
        return HttpResponse()
    else:
        return redirect("analysis:details", team_slug=team_slug, pk=group.analysis_id)
