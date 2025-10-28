from functools import cached_property

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView
from django_htmx.http import HttpResponseClientRedirect, HttpResponseClientRefresh
from django_tables2 import RequestConfig, SingleTableView

from apps.experiments.export import filtered_export_to_csv
from apps.experiments.models import Experiment
from apps.teams.mixins import LoginAndTeamRequiredMixin

from ..experiments.tables import ExperimentSessionsTable
from ..teams.decorators import login_and_team_required
from .forms import TranscriptAnalysisForm
from .models import AnalysisQuery, TranscriptAnalysis
from .tables import TranscriptAnalysisTable
from .tasks import process_transcript_analysis


class TranscriptAnalysisListView(LoginAndTeamRequiredMixin, SingleTableView):
    model = TranscriptAnalysis
    table_class = TranscriptAnalysisTable
    template_name = "analysis/list.html"

    def get(self, request, *args, **kwargs):
        if request.htmx:
            table = self.get_table(**self.get_table_kwargs())
            return render(request, "table/single_table.html", {"table": table})
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return TranscriptAnalysis.objects.filter(team=self.request.team)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_tab"] = "analysis"
        return context


class TranscriptAnalysisCreateView(LoginAndTeamRequiredMixin, CreateView):
    model = TranscriptAnalysis
    form_class = TranscriptAnalysisForm
    template_name = "analysis/create.html"

    @cached_property
    def experiment(self):
        return get_object_or_404(Experiment, id=self.kwargs.get("experiment_id"), team=self.request.team)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        kwargs["experiment"] = self.experiment
        kwargs["team"] = self.request.team
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_tab"] = "analysis"
        context["experiment"] = self.experiment
        context["session_limit"] = settings.ANALYTICS_MAX_SESSIONS
        return context

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        form.instance.experiment_id = self.kwargs.get("experiment_id")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("analysis:detail", args=[self.request.team.slug, self.object.id])


class TranscriptAnalysisDetailView(LoginAndTeamRequiredMixin, DetailView):
    model = TranscriptAnalysis
    template_name = "analysis/detail.html"

    def get(self, request, *args, **kwargs):
        if request.htmx:
            self.object = self.get_object()
            return render(request, "table/single_table.html", {"table": self.get_table()})
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return TranscriptAnalysis.objects.filter(team=self.request.team)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_tab"] = "analysis"
        if self.object.job_id and not self.object.is_complete and not self.object.is_failed:
            context["celery_job_id"] = self.object.job_id

        if results := self.object.result_file:
            with results.open("r") as file:
                context["results_preview"] = "".join(file.readlines()[:10])
        return context

    def get_table(self):
        queryset = self.object.sessions.all().annotate_with_last_message_created_at()
        table = ExperimentSessionsTable(data=queryset)
        return RequestConfig(self.request).configure(table)


class TranscriptAnalysisDeleteView(LoginAndTeamRequiredMixin, DeleteView):
    model = TranscriptAnalysis
    template_name = "analysis/confirm_delete.html"

    def get_queryset(self):
        return TranscriptAnalysis.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("analysis:list", args=[self.request.team.slug])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_tab"] = "analysis"
        return context


@login_and_team_required
def run_analysis(request, team_slug, pk):
    analysis = get_object_or_404(TranscriptAnalysis, id=pk, team__slug=team_slug)

    if analysis.is_processing:
        messages.error(request, "Analysis has already been completed or is in progress.")
        return HttpResponseClientRedirect(analysis.get_absolute_url())

    task = process_transcript_analysis.delay(analysis.id)
    analysis.job_id = task.id
    analysis.save(update_fields=["job_id"])

    return render(request, "analysis/components/progress.html", {"celery_job_id": task.id})


@login_and_team_required
def download_analysis_results(request, team_slug, pk):
    analysis = get_object_or_404(TranscriptAnalysis, id=pk, team__slug=team_slug)

    if not analysis.is_complete or not analysis.result_file:
        messages.error(request, "Analysis results are not available yet.")
        return redirect(analysis.get_absolute_url())

    response = FileResponse(analysis.result_file.open("rb"), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{analysis.name}_results.csv"'
    return response


@login_and_team_required
def export_sessions(request, team_slug, pk):
    analysis = get_object_or_404(TranscriptAnalysis, id=pk, team__slug=team_slug)
    sessions = analysis.sessions.all()
    csv_content = filtered_export_to_csv(
        analysis.experiment, sessions, translation_language=analysis.translation_language
    )

    response = HttpResponse(csv_content.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{analysis.name}_sessions_export.csv"'
    return response


@login_and_team_required
def clone(request, team_slug, pk):
    analysis = get_object_or_404(TranscriptAnalysis, id=pk, team__slug=team_slug)
    new_analysis = TranscriptAnalysis.objects.create(
        name=f"Copy of {analysis.name}",
        description=analysis.description,
        query_file=analysis.query_file,
        experiment=analysis.experiment,
        team=request.team,
        created_by=request.user,
        llm_provider_id=analysis.llm_provider_id,
        llm_provider_model_id=analysis.llm_provider_model_id,
    )
    new_analysis.sessions.set(analysis.sessions.all())

    for query in analysis.queries.all():
        AnalysisQuery.objects.create(
            analysis=new_analysis,
            name=query.name,
            prompt=query.prompt,
            output_format=query.output_format,
            order=query.order,
        )

    return redirect(new_analysis.get_absolute_url())


@login_and_team_required
def update_field(request, team_slug, pk):
    analysis = get_object_or_404(TranscriptAnalysis, id=pk, team__slug=team_slug)
    allowed_fields = {"name", "description"}

    if request.method == "POST":
        field_name = request.POST.get("field_name", "").strip()
        if field_name not in allowed_fields:
            return JsonResponse({"error": "Editing this field is not permitted."}, status=403)

        field_type = request.POST.get("field_type", "").strip()
        if not field_name:
            return JsonResponse({"error": "Field name is required."}, status=400)

        value = request.POST.get(field_name, "").strip()
        if value:
            setattr(analysis, field_name, value)
            analysis.save(update_fields=[field_name])
        return render(
            request,
            "analysis/components/editable_field.html",
            {
                "value": getattr(analysis, field_name),
                "field_name": field_name,
                "field_type": field_type,
                "object": analysis,
            },
        )

    field_name = request.GET.get("field_name", "")
    field_type = request.GET.get("field_type", "")

    return render(
        request,
        "analysis/components/edit_field.html",
        {
            "label": field_name.capitalize(),
            "value": getattr(analysis, field_name),
            "field_name": field_name,
            "field_type": field_type,
            "object": analysis,
        },
    )


@require_POST
@login_and_team_required
def add_query(request, team_slug, pk):
    analysis = get_object_or_404(TranscriptAnalysis, id=pk, team__slug=team_slug)

    name = request.POST.get("name", "")
    prompt = request.POST.get("prompt", "")
    output_format = request.POST.get("output_format", "")

    AnalysisQuery.objects.create(
        analysis=analysis,
        name=name,
        prompt=prompt,
        output_format=output_format,
        order=AnalysisQuery.objects.filter(analysis=analysis).count() + 1,
    )

    return HttpResponseClientRefresh()


@login_and_team_required
def update_query(request, team_slug, pk, query_id):
    query = get_object_or_404(AnalysisQuery, id=query_id, analysis_id=pk, analysis__team__slug=team_slug)
    analysis = query.analysis

    template = "analysis/components/query_edit.html"
    if request.method == "DELETE":
        query.delete()
        if not analysis.queries.exists():
            return HttpResponseClientRefresh()
        return HttpResponse()
    elif request.method == "POST":
        name = request.POST.get("name", "")
        prompt = request.POST.get("prompt", "")
        output_format = request.POST.get("output_format", "")
        order = int(request.POST.get("order", 0))

        query.name = name
        query.prompt = prompt
        query.output_format = output_format
        query.order = order
        query.save()
        template = "analysis/components/query.html"

    return render(
        request,
        template,
        {"query": query, "object": query.analysis},
    )
