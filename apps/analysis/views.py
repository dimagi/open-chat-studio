import json
from functools import cached_property

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView
from django_tables2 import SingleTableView

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
        return context

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        form.instance.experiment_id = self.kwargs.get("experiment_id")
        response = super().form_valid(form)

        task = process_transcript_analysis.delay(self.object.id)
        self.object.job_id = task.id
        self.object.save(update_fields=["job_id"])
        return response

    def get_success_url(self):
        return reverse("analysis:detail", args=[self.request.team.slug, self.object.id])


class TranscriptAnalysisDetailView(LoginAndTeamRequiredMixin, DetailView):
    model = TranscriptAnalysis
    template_name = "analysis/detail.html"

    def get_queryset(self):
        return TranscriptAnalysis.objects.filter(team=self.request.team)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_tab"] = "analysis"
        context["session_table"] = ExperimentSessionsTable(data=self.object.sessions.all())

        if self.object.job_id and not self.object.is_complete and not self.object.is_failed:
            context["celery_job_id"] = self.object.job_id

        # Add URLs for HTMX updates
        context["update_field_url"] = reverse("analysis:update_field", args=[self.request.team.slug, self.object.id])

        # Convert queries to JSON for Alpine.js
        queries_data = []
        for query in self.object.queries.all():
            queries_data.append(
                {
                    "id": query.id,
                    "name": query.name,
                    "prompt": query.prompt,
                    "output_format": query.output_format,
                    "order": query.order,
                }
            )
        context["queries_json"] = json.dumps(queries_data)

        return context


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

    if analysis.is_complete:
        messages.error(request, "Analysis has already been completed.")
        return redirect(analysis.get_absolute_url())

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

    response = HttpResponse(analysis.result_file.read(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{analysis.name}_results.csv"'
    return response


@login_and_team_required
def export_sessions(request, team_slug, pk):
    analysis = get_object_or_404(TranscriptAnalysis, id=pk, team__slug=team_slug)
    sessions = analysis.sessions.all()

    csv_content = filtered_export_to_csv(analysis.experiment, sessions)

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
@require_POST
def update_field(request, team_slug, pk):
    analysis = get_object_or_404(TranscriptAnalysis, id=pk, team__slug=team_slug)

    field_name = request.POST.get("field-name", "").strip()
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
            "label": field_name.capitalize(),
            "value": value,
            "field_name": field_name,
            "is_textarea": False,
            "update_url": reverse("analysis:update_field", args=[team_slug, pk]),
            "target_id": f"{field_name}-field",
        },
    )


@login_and_team_required
@require_POST
def update_queries(request, team_slug, pk):
    analysis = get_object_or_404(TranscriptAnalysis, id=pk, team__slug=team_slug)

    query_count = int(request.POST.get("query_count", 0))

    # Track existing query IDs to identify which ones to delete
    existing_query_ids = set(analysis.queries.values_list("id", flat=True))
    updated_query_ids = set()

    # Update/create queries
    for i in range(query_count):
        query_id = request.POST.get(f"query_id_{i}", "")
        name = request.POST.get(f"query_name_{i}", "")
        prompt = request.POST.get(f"query_prompt_{i}", "")
        output_format = request.POST.get(f"query_output_format_{i}", "")
        order = int(request.POST.get(f"query_order_{i}", i))

        # For existing queries
        if query_id and not query_id.startswith("new-") and query_id.isdigit():
            query_id = int(query_id)
            try:
                query = AnalysisQuery.objects.get(id=query_id, analysis=analysis)
                query.name = name
                query.prompt = prompt
                query.output_format = output_format
                query.order = order
                query.save()
                updated_query_ids.add(query_id)
            except AnalysisQuery.DoesNotExist:
                pass
        # For new queries
        elif prompt:  # Only create if there's at least a prompt
            new_query = AnalysisQuery.objects.create(
                analysis=analysis,
                name=name,
                prompt=prompt,
                output_format=output_format,
                order=order,
            )
            updated_query_ids.add(new_query.id)

    # Delete queries that weren't updated
    queries_to_delete = existing_query_ids - updated_query_ids
    AnalysisQuery.objects.filter(id__in=queries_to_delete).delete()

    # Prepare data for the template
    queries_data = []
    for query in analysis.queries.all():
        queries_data.append(
            {
                "id": query.id,
                "name": query.name,
                "prompt": query.prompt,
                "output_format": query.output_format,
                "order": query.order,
            }
        )

    queries_json = json.dumps(queries_data)

    return render(
        request,
        "analysis/components/editable_queries.html",
        {
            "object": analysis,
            "team": analysis.team,
            "queries_json": queries_json,
        },
    )
