from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.evaluations.forms import EvaluationDatasetForm, EvaluationMessageForm
from apps.evaluations.models import EvaluationDataset
from apps.evaluations.tables import EvaluationDatasetTable, EvaluationSessionsTable
from apps.experiments.filters import apply_dynamic_filters
from apps.experiments.models import ExperimentSession
from apps.teams.mixins import LoginAndTeamRequiredMixin


class DatasetHome(LoginAndTeamRequiredMixin, TemplateView):  # , PermissionRequiredMixin
    # permission_required = "pipelines.view_pipeline"
    template_name = "generic/object_home.html"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "evaluation_datasets",
            "title": "Datasets",
            "new_object_url": reverse("evaluations:dataset_new", args=[team_slug]),
            "table_url": reverse("evaluations:dataset_table", args=[team_slug]),
            # "title_help_content": render_help_with_link(
            #     "Pipelines allow you to create more complex bots by combining one or more steps together.", "pipelines"  # noqa
            # ),
        }


class DatasetTableView(SingleTableView, PermissionRequiredMixin):
    # permission_required = "pipelines.view_pipeline"
    model = EvaluationDataset
    paginate_by = 25
    table_class = EvaluationDatasetTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return (
            EvaluationDataset.objects.filter(team=self.request.team)
            # .annotate(run_count=Count("runs"))
            # .order_by("name")
        )


class CreateDataset(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    # permission_required = "pipelines.add_pipeline"
    template_name = "evaluations/dataset_form.html"
    model = EvaluationDataset
    form_class = EvaluationDatasetForm
    extra_context = {
        "title": "Create Dataset",
        "button_text": "Create",
        "active_tab": "evaluation_datasets",
        "new_message_form": EvaluationMessageForm(),
    }

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "team": self.request.team}

    def get_success_url(self):
        return reverse("evaluations:dataset_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class EditDataset(UpdateView):
    model = EvaluationDataset
    form_class = EvaluationDatasetForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Update Dataset",
        "button_text": "Update",
        "active_tab": "evaluation_datasets",
    }

    def get_queryset(self):
        return EvaluationDataset.objects.filter(team=self.request.team)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "team": self.request.team}

    def get_success_url(self):
        return reverse("evaluations:dataset_home", args=[self.request.team.slug])


class DatasetSessionsTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    model = ExperimentSession
    paginate_by = 20
    table_class = EvaluationSessionsTable
    template_name = "table/single_table.html"
    permission_required = "experiments.view_experimentsession"

    def get_queryset(self):
        query_set = (
            ExperimentSession.objects.with_last_message_created_at()
            .filter(team=self.request.team)
            .select_related("participant__user")
            .order_by("experiment__name")
        )
        query_set = apply_dynamic_filters(query_set, self.request)
        return query_set


# TODO Permissions
def session_messages_json(request, team_slug: str, session_id: str):
    session = get_object_or_404(ExperimentSession, team__slug=team_slug, external_id=session_id)
    messages = session.chat.messages.values("id", "message_type", "content").order_by("id")
    return JsonResponse(list(messages), safe=False)
