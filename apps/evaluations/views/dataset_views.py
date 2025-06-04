from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessageType
from apps.evaluations.forms import EvaluationDatasetForm, EvaluationDatasetFromSessionsForm, EvaluationMessageForm
from apps.evaluations.models import EvaluationDataset
from apps.evaluations.tables import EvaluationDatasetTable, EvaluationSessionsSelectionTable, EvaluationSessionsTable
from apps.experiments.filters import DATE_RANGE_OPTIONS, FIELD_TYPE_FILTERS, apply_dynamic_filters
from apps.experiments.models import Experiment, ExperimentSession
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
        "button_text": "Create Dataset",
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


class CreateDatasetFromSessions(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    # permission_required = "pipelines.add_pipeline"
    template_name = "evaluations/dataset_from_sessions_form.html"
    model = EvaluationDataset
    form_class = EvaluationDatasetFromSessionsForm
    extra_context = {
        "title": "Create Dataset from Sessions",
        "button_text": "Create Dataset",
        "active_tab": "evaluation_datasets",
    }

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "team": self.request.team}

    def get_initial(self):
        """Support pre-selected sessions via URL parameters."""
        initial = super().get_initial()
        preselected_sessions = self.request.GET.get("sessions", "")
        if preselected_sessions:
            initial["session_ids"] = preselected_sessions
        return initial

    def _get_filter_context_data(self):
        experiments = Experiment.objects.filter(team=self.request.team).values("id", "name").order_by("name")
        experiment_list = [{"id": exp["id"], "name": exp["name"]} for exp in experiments]

        channel_list = ChannelPlatform.for_filter(self.request.team)
        available_tags = [tag.name for tag in self.request.team.tag_set.filter(is_system_tag=False)]

        experiment_versions = []
        for experiment in Experiment.objects.filter(team=self.request.team):
            experiment_versions.extend(experiment.get_version_name_list())
        experiment_versions = list(set(experiment_versions))

        return {
            "available_tags": available_tags,
            "experiment_versions": experiment_versions,
            "experiment_list": experiment_list,
            "field_type_filters": FIELD_TYPE_FILTERS,
            "channel_list": channel_list,
            "date_range_options": DATE_RANGE_OPTIONS,
            "filter_columns": [
                "experiment",
                "participant",
                "last_message",
                "first_message",
                "tags",
                "versions",
                "channels",
            ],
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._get_filter_context_data())
        return context

    def get_success_url(self):
        return reverse("evaluations:dataset_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        return super().form_valid(form)


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


class DatasetSessionsSelectionTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    """Table view for selecting sessions to create a dataset from."""

    model = ExperimentSession
    paginate_by = 3
    table_class = EvaluationSessionsSelectionTable
    template_name = "table/single_table.html"
    permission_required = "experiments.view_experimentsession"

    def get_queryset(self):
        query_set = (
            ExperimentSession.objects.with_last_message_created_at()
            .filter(team=self.request.team)
            .select_related("participant__user", "chat")
            .prefetch_related("chat__messages")
            .order_by("experiment__name")
        )
        query_set = apply_dynamic_filters(query_set, self.request)
        return query_set


# TODO Permissions
def session_messages_json(request, team_slug: str, session_id: str):
    session = get_object_or_404(ExperimentSession, team__slug=team_slug, external_id=session_id)
    messages = session.chat.messages.order_by("created_at")

    pairs = []
    i = 0
    while i < len(messages) - 1:
        m1, m2 = messages[i], messages[i + 1]
        if m1.message_type == ChatMessageType.HUMAN and m2.message_type == ChatMessageType.AI:
            pairs.append(
                {
                    "human": {"id": m1.id, "content": m1.content},
                    "ai": {"id": m2.id, "content": m2.content},
                    "context": {
                        "current_datetime": m1.created_at,
                        "history": "\n".join(
                            f"{message.get_message_type_display()}: {message.content}"
                            for message in session.chat.messages.filter(created_at__lt=m1.created_at)
                        ),
                    },
                }
            )
            i += 2
        else:
            # Skip bad/malformed pairs
            i += 1
    return JsonResponse(pairs, safe=False)
