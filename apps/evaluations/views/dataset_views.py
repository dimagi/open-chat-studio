import json

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from django.views.generic import CreateView, DeleteView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.channels.models import ChannelPlatform
from apps.evaluations.forms import EvaluationDatasetEditForm, EvaluationDatasetForm
from apps.evaluations.models import EvaluationDataset, EvaluationMessage, EvaluationMessageContent
from apps.evaluations.tables import (
    DatasetMessagesTable,
    EvaluationDatasetTable,
    EvaluationSessionsSelectionTable,
)
from apps.experiments.filters import DATE_RANGE_OPTIONS, FIELD_TYPE_FILTERS, DynamicExperimentSessionFilter
from apps.experiments.models import Experiment, ExperimentSession
from apps.teams.decorators import login_and_team_required
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
            session_filter = DynamicExperimentSessionFilter(queryset, self.request.GET, timezone)
            filtered_queryset = session_filter.apply()
            filtered_session_ids = ",".join(str(session.external_id) for session in filtered_queryset)
            if filtered_session_ids:
                initial["session_ids"] = filtered_session_ids

        return initial

    def _get_filter_context_data(self):
        experiments = (
            Experiment.objects.working_versions_queryset()
            .filter(team=self.request.team)
            .values("id", "name")
            .order_by("name")
        )
        experiment_list = [{"id": exp["id"], "label": exp["name"]} for exp in experiments]

        channel_list = ChannelPlatform.for_filter(self.request.team)
        available_tags = [tag.name for tag in self.request.team.tag_set.filter(is_system_tag=False)]

        experiment_versions = []
        for experiment in Experiment.objects.filter(team=self.request.team):
            experiment_versions.extend(experiment.get_version_name_list())
        experiment_versions = list(set(experiment_versions))

        return {
            "df_available_tags": available_tags,
            "df_experiment_versions": experiment_versions,
            "df_experiment_list": experiment_list,
            "df_field_type_filters": FIELD_TYPE_FILTERS,
            "df_channel_list": channel_list,
            "df_date_range_options": DATE_RANGE_OPTIONS,
            "df_filter_columns": DynamicExperimentSessionFilter.columns,
            "df_date_range_column_name": "last_message",
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
            .select_related("participant__user", "chat", "experiment")
            .annotate(message_count=Count("chat__messages"))
            .order_by("experiment__name")
        )
        timezone = self.request.session.get("detected_tz", None)
        session_filter = DynamicExperimentSessionFilter(query_set, self.request.GET, timezone)
        query_set = session_filter.apply()
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

        return EvaluationMessage.objects.filter(
            evaluationdataset__id=dataset_id, evaluationdataset__team=self.request.team
        ).order_by("id")


@login_and_team_required
@require_POST
def add_message_to_dataset(request, team_slug: str, dataset_id: int):
    """Add a new message pair to an existing dataset and return updated table."""
    try:
        dataset = get_object_or_404(EvaluationDataset, id=dataset_id, team__slug=team_slug)

        human_message = request.POST.get("human_message", "").strip()
        ai_message = request.POST.get("ai_message", "").strip()
        context_json = request.POST.get("context", "{}")

        if not human_message or not ai_message:
            return HttpResponse("Both human and AI messages are required", status=400)

        if context_json.strip():
            try:
                context = json.loads(context_json)
            except json.JSONDecodeError:
                return HttpResponse("Invalid JSON format in context", status=400)
        else:
            context = {}

        message = EvaluationMessage.objects.create(
            input=EvaluationMessageContent(content=human_message, role="human").model_dump(),
            output=EvaluationMessageContent(content=ai_message, role="ai").model_dump(),
            context=context,
            metadata={"created_mode": "manual"},
        )

        dataset.messages.add(message)

        table_view = DatasetMessagesTableView()
        table_view.request = request
        table_view.kwargs = {"dataset_id": dataset_id}

        queryset = table_view.get_queryset()
        table = table_view.table_class(queryset)

        return render(request, "table/single_table.html", {"table": table})

    except Exception:
        return HttpResponse("An unknown error ocurred", status=500)


@login_and_team_required
def edit_message_modal(request, team_slug, message_id):
    """Serve the edit modal content with message data"""
    message = get_object_or_404(EvaluationMessage, id=message_id, evaluationdataset__team__slug=team_slug)

    # Prepare form data
    form_data = {
        "human": message.input.get("content", ""),
        "ai": message.output.get("content", ""),
        "context": json.dumps(message.context, indent=2) if message.context else "{}",
    }

    update_url = reverse("evaluations:update_message", args=[team_slug, message_id])

    return render(
        request,
        "evaluations/edit_message_modal_content.html",
        {
            "message": message,
            "form_data": form_data,
            "update_url": update_url,
        },
    )


@login_and_team_required
@require_POST
def update_message(request, team_slug, message_id):
    """Handle form submission to update message"""
    message = get_object_or_404(EvaluationMessage, id=message_id, evaluationdataset__team__slug=team_slug)

    human_content = request.POST.get("human_content", "").strip()
    ai_content = request.POST.get("ai_content", "").strip()
    context_str = request.POST.get("context", "").strip()

    errors = {}
    if not human_content:
        errors["human"] = "Human message is required"
    if not ai_content:
        errors["ai"] = "AI message is required"

    context_data = {}
    if context_str:
        try:
            context_data = json.loads(context_str)
        except json.JSONDecodeError:
            errors["context"] = "Invalid JSON format"

    if errors:
        form_data = {"human": human_content, "ai": ai_content, "context": context_str}
        update_url = reverse("evaluations:update_message", args=[team_slug, message_id])

        return render(
            request,
            "evaluations/edit_message_modal_content.html",
            {
                "message": message,
                "form_data": form_data,
                "update_url": update_url,
                "errors": errors,
            },
            status=400,
        )

    message.input = EvaluationMessageContent(content=human_content, role="human").model_dump()
    message.output = EvaluationMessageContent(content=ai_content, role="ai").model_dump()
    message.context = context_data

    # Clear chat message references since this is now manually edited
    message.input_chat_message = None
    message.expected_output_chat_message = None
    message.metadata = message.metadata or {}
    message.metadata["session_id"] = None
    message.metadata["experiment_id"] = None

    message.save()

    return HttpResponse("", status=200)


@login_and_team_required
@require_http_methods(["DELETE"])
def delete_message(request, team_slug, message_id):
    """Delete a message from the dataset"""
    message = get_object_or_404(EvaluationMessage, id=message_id, evaluationdataset__team__slug=team_slug)
    message.delete()
    return HttpResponse("", status=200)
