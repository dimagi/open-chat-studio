import json

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.generic import CreateView, DeleteView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessageType
from apps.evaluations.forms import EvaluationDatasetEditForm, EvaluationDatasetForm
from apps.evaluations.models import EvaluationDataset, EvaluationMessage
from apps.evaluations.tables import (
    DatasetMessagesTable,
    EvaluationDatasetTable,
    EvaluationSessionsSelectionTable,
    EvaluationSessionsTable,
)
from apps.experiments.filters import DATE_RANGE_OPTIONS, FIELD_TYPE_FILTERS, apply_dynamic_filters
from apps.experiments.models import Experiment, ExperimentSession
from apps.teams.mixins import LoginAndTeamRequiredMixin


class DatasetHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    permission_required = "evaluations.view_evaluationdataset"
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
    permission_required = "evaluations.view_evaluationdataset"
    model = EvaluationDataset
    paginate_by = 25
    table_class = EvaluationDatasetTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        from django.db.models import Count

        return (
            EvaluationDataset.objects.filter(team=self.request.team)
            .annotate(message_count=Count("messages"))
            .order_by("name")
        )


class EditDataset(LoginAndTeamRequiredMixin, UpdateView, PermissionRequiredMixin):
    permission_required = "evaluations.change_evaluationdataset"
    model = EvaluationDataset
    form_class = EvaluationDatasetEditForm
    template_name = "evaluations/dataset_edit.html"
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


class DeleteDataset(LoginAndTeamRequiredMixin, DeleteView, PermissionRequiredMixin):
    permission_required = "evaluations.delete_evaluationdataset"
    model = EvaluationDataset

    def get_queryset(self):
        return EvaluationDataset.objects.filter(team=self.request.team)

    def delete(self, request, *args, **kwargs):
        """Handle AJAX delete requests."""
        self.object = self.get_object()
        self.object.delete()

        return HttpResponse(status=200)


class CreateDataset(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    permission_required = "evaluations.add_evaluationdataset"
    template_name = "evaluations/dataset_from_sessions_form.html"
    model = EvaluationDataset
    form_class = EvaluationDatasetForm
    extra_context = {
        "title": "Create Dataset",
        "button_text": "Create Dataset",
        "active_tab": "evaluation_datasets",
    }

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "team": self.request.team}

    def get_initial(self):
        """Support filters from experiment session list via URL parameters."""
        initial = super().get_initial()

        # Only pre-populate sessions if there are explicit filter parameters in the URL
        # This prevents selecting all sessions when default filters are applied
        has_explicit_filters = any(key.startswith("filter_") for key in self.request.GET)

        if has_explicit_filters:
            # Apply the same filters to get the filtered session IDs
            queryset = (
                ExperimentSession.objects.with_last_message_created_at()
                .filter(team=self.request.team)
                .select_related("participant__user")
            )
            timezone = self.request.session.get("detected_tz", None)
            filtered_queryset = apply_dynamic_filters(queryset, self.request.GET, timezone)
            filtered_session_ids = ",".join(str(session.external_id) for session in filtered_queryset)
            if filtered_session_ids:
                initial["session_ids"] = filtered_session_ids

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
        timezone = self.request.session.get("detected_tz", None)
        query_set = apply_dynamic_filters(query_set, self.request.GET, timezone)
        return query_set


class DatasetSessionsSelectionTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    """Table view for selecting sessions to create a dataset from."""

    model = ExperimentSession
    paginate_by = 20
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
        timezone = self.request.session.get("detected_tz", None)
        query_set = apply_dynamic_filters(query_set, self.request.GET, timezone)
        return query_set


class DatasetMessagesTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    """Table view for dataset messages with pagination."""

    model = EvaluationMessage
    paginate_by = 10
    table_class = DatasetMessagesTable
    template_name = "table/single_table.html"
    permission_required = "evaluations.view_evaluationdataset"

    def get_queryset(self):
        dataset_id = self.kwargs.get("dataset_id")
        # Verify the dataset exists and user has access
        get_object_or_404(EvaluationDataset, id=dataset_id, team=self.request.team)

        # Query messages that belong to this dataset with related chat messages and sessions
        return (
            EvaluationMessage.objects.filter(
                evaluationdataset__id=dataset_id, evaluationdataset__team=self.request.team
            )
            .select_related("human_chat_message__chat__experiment_session__experiment")
            .order_by("id")
        )


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


@require_http_methods(["POST"])
@csrf_exempt  # We'll handle CSRF via HTMX headers
def update_message_content(request, team_slug: str, message_id: int):
    """Update human or AI message content and remove chat message relationship if edited."""
    try:
        # Get the message and verify team access
        message = get_object_or_404(
            EvaluationMessage.objects.filter(id=message_id, evaluationdataset__team__slug=team_slug)
        )

        message_type = request.POST.get("message_type")
        new_content = request.POST.get("content", "").strip()

        if not new_content:
            return JsonResponse({"error": "Content cannot be empty"}, status=400)

        if message_type == "human":
            if message.human_message_content != new_content:
                message.human_message_content = new_content
                # Remove both foreign key relationships since content was manually edited
                message.human_chat_message = None
                message.ai_chat_message = None
        elif message_type == "ai":
            if message.ai_message_content != new_content:
                message.ai_message_content = new_content
                # Remove both foreign key relationships since content was manually edited
                message.human_chat_message = None
                message.ai_chat_message = None
        else:
            return JsonResponse({"error": "Invalid message type"}, status=400)

        message.save()

        return JsonResponse({"success": True, "content": new_content, "message_type": message_type})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_http_methods(["POST"])
@csrf_exempt  # We'll handle CSRF via HTMX headers
def add_message_to_dataset(request, team_slug: str, dataset_id: int):
    """Add a new message pair to an existing dataset."""
    try:
        # Get the dataset and verify team access
        dataset = get_object_or_404(EvaluationDataset.objects.filter(id=dataset_id, team__slug=team_slug))

        human_message = request.POST.get("human_message", "").strip()
        ai_message = request.POST.get("ai_message", "").strip()
        context_json = request.POST.get("context", "{}")

        if not human_message or not ai_message:
            return JsonResponse({"error": "Both human and AI messages are required"}, status=400)

        # Parse context JSON
        try:
            context = json.loads(context_json)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON format in context"}, status=400)

        # Create new evaluation message
        message = EvaluationMessage.objects.create(
            human_message_content=human_message,
            ai_message_content=ai_message,
            context=context,
            metadata={"created_mode": "manual"},
        )

        # Add to dataset
        dataset.messages.add(message)

        return JsonResponse({"success": True, "message_id": message.id})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
