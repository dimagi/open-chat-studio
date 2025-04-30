from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import CreateView, DeleteView, DetailView
from django_tables2 import SingleTableView

from apps.experiments.export import filtered_export_to_csv
from apps.experiments.models import Experiment
from apps.teams.mixins import LoginAndTeamRequiredMixin

from ..experiments.tables import ExperimentSessionsTable
from .forms import TranscriptAnalysisForm
from .models import TranscriptAnalysis
from .tables import TranscriptAnalysisTable
from .tasks import process_transcript_analysis


class TranscriptAnalysisListView(LoginAndTeamRequiredMixin, SingleTableView):
    model = TranscriptAnalysis
    table_class = TranscriptAnalysisTable
    template_name = "analysis/list.html"

    def get_queryset(self):
        return TranscriptAnalysis.objects.filter(team=self.request.team)


class TranscriptAnalysisCreateView(LoginAndTeamRequiredMixin, CreateView):
    model = TranscriptAnalysis
    form_class = TranscriptAnalysisForm
    template_name = "analysis/create.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["experiment_id"] = self.kwargs.get("experiment_id")
        kwargs["team"] = self.request.team
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["experiment"] = get_object_or_404(
            Experiment, id=self.kwargs.get("experiment_id"), team=self.request.team
        )
        return context

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        form.instance.experiment_id = self.kwargs.get("experiment_id")
        response = super().form_valid(form)

        task = process_transcript_analysis.delay(self.object.id)
        self.object.job_id = task.id
        self.object.save(update_fields=["job_id"])

        messages.success(self.request, "Analysis job created and processing started.")
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
        context["session_table"] = ExperimentSessionsTable(data=self.object.sessions.all())

        if self.object.job_id and not self.object.is_complete and not self.object.is_failed:
            context["celery_job_id"] = self.object.job_id

        return context


class TranscriptAnalysisDeleteView(LoginAndTeamRequiredMixin, DeleteView):
    model = TranscriptAnalysis
    template_name = "analysis/confirm_delete.html"

    def get_queryset(self):
        return TranscriptAnalysis.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("analysis:list", args=[self.request.team.slug])


def download_analysis_results(request, team_slug, pk):
    analysis = get_object_or_404(TranscriptAnalysis, id=pk, team__slug=team_slug)

    if not analysis.is_complete or not analysis.result_file:
        messages.error(request, "Analysis results are not available yet.")
        return redirect(analysis.get_absolute_url())

    response = HttpResponse(analysis.result_file.read(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{analysis.name}_results.csv"'
    return response


def export_sessions(request, team_slug, pk):
    analysis = get_object_or_404(TranscriptAnalysis, id=pk, team__slug=team_slug)
    sessions = analysis.sessions.all()

    csv_content = filtered_export_to_csv(analysis.experiment, sessions)

    response = HttpResponse(csv_content.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{analysis.name}_sessions_export.csv"'
    return response
