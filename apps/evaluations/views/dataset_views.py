import csv
import json
import logging
from datetime import timedelta
from io import StringIO
from itertools import islice

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.http import require_http_methods, require_POST
from django.views.generic import CreateView, DeleteView, TemplateView, UpdateView
from django_tables2 import LazyPaginator, SingleTableView

from apps.chat.models import ChatMessage
from apps.evaluations.forms import EvaluationDatasetEditForm, EvaluationDatasetForm
from apps.evaluations.models import (
    DatasetCreationStatus,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationMessageContent,
)
from apps.evaluations.tables import (
    DatasetMessagesTable,
    EvaluationDatasetTable,
    EvaluationSessionsSelectionTable,
)
from apps.evaluations.tasks import update_dataset_from_csv_task
from apps.evaluations.utils import (
    generate_csv_column_suggestions,
    make_evaluation_messages_from_sessions,
    normalize_json_quotes,
    parse_history_text,
)
from apps.experiments.filters import (
    ChatMessageFilter,
    ExperimentSessionFilter,
    get_filter_context_data,
)
from apps.experiments.models import ExperimentSession
from apps.files.models import File, FilePurpose
from apps.filters.models import FilterSet
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin
from apps.web.dynamic_filters.datastructures import FilterParams
from apps.web.waf import WafRule, waf_allow

logger = logging.getLogger("ocs.evaluations")


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
    table_class = EvaluationDatasetTable
    template_name = "table/single_table.html"

    def get_queryset(self):
        return (
            EvaluationDataset.objects.filter(team=self.request.team)
            .annotate(message_count=Count("messages"))
            .order_by("-created_at")
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
        kwargs = super().get_form_kwargs()
        kwargs["team"] = self.request.team
        kwargs["filter_params"] = FilterParams.from_request(self.request)
        kwargs["timezone"] = self.request.session.get("detected_tz", None)
        return kwargs

    def _get_filter_context_data(self):
        table_url = reverse("evaluations:dataset_sessions_selection_list", args=[self.request.team.slug])
        return get_filter_context_data(
            self.request.team,
            ExperimentSessionFilter.columns(self.request.team),
            "last_message",
            table_url,
            "sessions-table",
            table_type=FilterSet.TableType.DATASETS,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._get_filter_context_data())
        context["celery_job_id"] = self.object.job_id
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Dataset updated successfully!")
        return response

    def get_success_url(self):
        return reverse("evaluations:dataset_edit", args=[self.request.team.slug, self.object.pk])


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


@waf_allow(WafRule.SizeRestrictions_BODY)
class CreateDataset(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    permission_required = "evaluations.add_evaluationdataset"
    template_name = "evaluations/dataset_create_form.html"
    model = EvaluationDataset
    form_class = EvaluationDatasetForm
    extra_context = {
        "title": "Create Dataset",
        "button_text": "Create Dataset",
        "active_tab": "evaluation_datasets",
        "form_attrs": {"id": "dataset-create-form"},
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["team"] = self.request.team

        # Pass current filter parameters to the form
        kwargs["filter_params"] = FilterParams.from_request(self.request)
        kwargs["timezone"] = self.request.session.get("detected_tz", None)

        return kwargs

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
            session_filter = ExperimentSessionFilter()
            filtered_queryset = session_filter.apply(
                queryset, filter_params=FilterParams.from_request(self.request), timezone=timezone
            )
            filtered_session_ids = ",".join(str(session.external_id) for session in filtered_queryset)
            if filtered_session_ids:
                initial["session_ids"] = filtered_session_ids

        return initial

    def _get_filter_context_data(self):
        table_url = reverse("evaluations:dataset_sessions_selection_list", args=[self.request.team.slug])
        context = get_filter_context_data(
            self.request.team,
            columns=ExperimentSessionFilter.columns(self.request.team),
            date_range_column="last_message",
            table_url=table_url,
            table_container_id="sessions-table",
            table_type=FilterSet.TableType.DATASETS,
        )
        return context

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._get_filter_context_data())
        return context

    def get_success_url(self):
        return reverse("evaluations:dataset_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        response = super().form_valid(form)

        mode = form.cleaned_data.get("mode")
        if mode == "manual":
            messages.success(self.request, "Dataset created successfully!")
        else:
            messages.success(self.request, "Dataset created! Messages are being created in the background.")

        return response


class DatasetSessionsSelectionTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    """Table view for selecting sessions to create a dataset from."""

    model = ExperimentSession
    table_class = EvaluationSessionsSelectionTable
    template_name = "table/single_table_lazy_pagination.html"
    permission_required = "experiments.view_experimentsession"
    paginator_class = LazyPaginator

    def get_queryset(self):
        queryset = get_base_session_queryset(self.request)

        # Get filter params for message count
        timezone = self.request.session.get("detected_tz", None)
        filter_params = FilterParams.from_request(self.request)
        message_filter = ChatMessageFilter()
        base_messages = ChatMessage.objects.filter(chat_id=OuterRef("chat_id"))
        filtered_messages = message_filter.apply(base_messages, filter_params, timezone)

        # Add expensive annotations only to paginated data
        queryset = queryset.annotate_with_versions_list().annotate(
            message_count=Coalesce(
                Count("chat__messages", filter=Q(chat__messages__in=filtered_messages.values("pk")), distinct=True),
                0,
            )
        )
        return queryset.select_related("team", "participant__user", "chat", "experiment").order_by("experiment__name")


def get_base_session_queryset(request):
    """Returns a lightweight queryset for counting. Expensive annotations are added in get_table_data()."""
    timezone = request.session.get("detected_tz", None)
    filter_params = FilterParams.from_request(request)

    # Get filtered message IDs more efficiently
    message_filter = ChatMessageFilter()
    base_messages = ChatMessage.objects.filter(chat_id=OuterRef("chat_id"))
    filtered_messages = message_filter.apply(base_messages, filter_params, timezone)

    # Use Exists for filtering instead of Count with IN subquery - avoids cartesian product
    has_messages = Exists(filtered_messages)

    # Build the query with basic filtering only
    query_set = ExperimentSession.objects.filter(team=request.team).filter(has_messages)

    # Apply session filter (this will add first_message_created_at and last_message_created_at)
    session_filter = ExperimentSessionFilter()
    query_set = session_filter.apply(query_set, filter_params=filter_params, timezone=timezone)

    return query_set


@login_and_team_required
@permission_required("experiments.view_experimentsession")
def dataset_sessions_selection_json(request, team_slug: str):
    query_set = get_base_session_queryset(request)
    session_keys = list(query_set.values_list("external_id", flat=True))
    return JsonResponse(session_keys, safe=False)


class DatasetMessagesTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    """Table view for dataset messages with pagination."""

    model = EvaluationMessage
    table_class = DatasetMessagesTable
    table_pagination = {"per_page": 10}
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

        # Clear any previous error states when manually adding a message
        if dataset.is_failed or dataset.error_message:
            dataset.error_message = ""
            dataset.status = DatasetCreationStatus.COMPLETED
            dataset.save(update_fields=["error_message", "status"])

        form_data = _get_message_form_data(request)
        errors, data = _get_message_data_and_errors(form_data)
        if errors:
            message = "Errors:\n" + "\n".join([f"{key}: {value}" for key, value in errors.items()])
            return HttpResponse(escape(message), status=400)

        message = EvaluationMessage.objects.create(**data, metadata={"created_mode": "manual"})

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
        "participant_data": json.dumps(message.participant_data, indent=2) if message.participant_data else "{}",
        "session_state": json.dumps(message.session_state, indent=2) if message.session_state else "{}",
        "history_text": message.full_history,
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

    form_data = _get_message_form_data(request)
    errors, data = _get_message_data_and_errors(form_data)

    if errors:
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
            status=200,
        )

    for attr, val in data.items():
        setattr(message, attr, val)

    # Clear chat message references since this is now manually edited
    message.input_chat_message = None
    message.expected_output_chat_message = None
    message.metadata = message.metadata or {}
    message.metadata["session_id"] = None
    message.metadata["experiment_id"] = None

    message.save()

    return HttpResponse("", status=200)


def _get_message_data_and_errors(form_data: dict) -> tuple[dict, dict]:
    errors = {}
    if not form_data["human"]:
        errors["human"] = "Human message is required"
    if not form_data["ai"]:
        errors["ai"] = "AI message is required"

    def _get_json_var(name):
        json_val = form_data[name]
        if json_val.strip():
            try:
                return json.loads(json_val)
            except json.JSONDecodeError:
                errors[name] = "Invalid JSON format"
                return None
        else:
            return {}

    context = _get_json_var("context")
    participant_data = _get_json_var("participant_data")
    session_state = _get_json_var("session_state")

    # Parse history text if provided
    history_data = []
    if form_data["history_text"]:
        try:
            history_data = parse_history_text(form_data["history_text"])
        except Exception:
            errors["history_text"] = "Invalid history format"

    if errors:
        return errors, {}

    return errors, {
        "input": EvaluationMessageContent(content=form_data["human"], role="human").model_dump(),
        "output": EvaluationMessageContent(content=form_data["ai"], role="ai").model_dump(),
        "context": context,
        "participant_data": participant_data,
        "session_state": session_state,
        "history": history_data,
    }


def _get_message_form_data(request) -> dict:
    return {
        "human": request.POST.get("human_message", "").strip(),
        "ai": request.POST.get("ai_message", "").strip(),
        "history_text": request.POST.get("history_text", "").strip(),
        "context": request.POST.get("context", "{}"),
        "participant_data": request.POST.get("participant_data", "{}"),
        "session_state": request.POST.get("session_state", "{}"),
    }


@login_and_team_required
@require_http_methods(["DELETE"])
def delete_message(request, team_slug, message_id):
    """Delete a message from the dataset"""
    message = get_object_or_404(EvaluationMessage, id=message_id, evaluationdataset__team__slug=team_slug)
    message.delete()
    return HttpResponse("", status=200)


@waf_allow(WafRule.SizeRestrictions_BODY)
@login_and_team_required
@require_POST
def parse_csv_columns(request, team_slug: str):
    """Parse uploaded CSV, save to File model, and return column names and sample data."""
    try:
        csv_file = request.FILES.get("csv_file")
        if not csv_file:
            return JsonResponse({"error": "No CSV file provided"}, status=400)

        file_content = csv_file.read().decode("utf-8")
        csv_reader = csv.DictReader(StringIO(file_content))
        columns = csv_reader.fieldnames or []

        sample_rows = list(islice(csv_reader, 3))

        # This is for proper display in the sample
        for row in sample_rows:
            for key, value in row.items():
                if isinstance(value, str):
                    value_stripped = value.strip()
                    if value_stripped.startswith(("{", "[")):
                        row[key] = normalize_json_quotes(value)

        total_rows = len(sample_rows) + sum(1 for _ in csv_reader)

        suggestions = generate_csv_column_suggestions(columns)

        csv_file.seek(0)
        file_instance = File.create(
            filename=csv_file.name,
            file_obj=csv_file,
            team_id=request.team.id,
            metadata={
                "upload_timestamp": timezone.now().isoformat(),
            },
            purpose=FilePurpose.EVALUATION_DATASET,
            expiry_date=timezone.now() + timedelta(days=3),
        )

        return JsonResponse(
            {
                "columns": columns,
                "sample_rows": sample_rows,
                "file_id": file_instance.id,
                "total_rows": total_rows,
                "suggestions": suggestions,
            }
        )

    except Exception:
        logger.warning("Error parsing CSV")
        return JsonResponse({"error": "An error occurred while parsing the CSV file."}, status=400)


@login_and_team_required
def download_dataset_csv(request, team_slug: str, pk: int):
    """Download dataset as CSV with expanded context and metadata columns."""
    dataset = get_object_or_404(EvaluationDataset, id=pk, team__slug=team_slug)

    messages = dataset.messages.order_by("id").all()
    if not messages:
        # Return empty CSV with headers
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f"attachment; filename={dataset.name}_dataset.csv"
        writer = csv.writer(response)
        writer.writerow(["id", "input_content", "output_content", "history"])
        return response

    context_keys = {key for message in messages if message.context for key in message.context}
    participant_data_keys = {
        key for message in messages if message.participant_data for key in message.participant_data
    }
    session_state_keys = {key for message in messages if message.session_state for key in message.session_state}

    context_keys = sorted(context_keys)
    participant_data_keys = sorted(participant_data_keys)
    session_state_keys = sorted(session_state_keys)

    headers = ["id", "input_content", "output_content"]
    headers.extend([f"context.{key}" for key in context_keys])
    headers.extend([f"participant_data.{key}" for key in participant_data_keys])
    headers.extend([f"session_state.{key}" for key in session_state_keys])
    headers.append("history")

    filename = f"{dataset.name}_dataset.csv"
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f"attachment; filename={filename}"
    writer = csv.writer(response)

    writer.writerow(headers)

    def _serialize_value(value):
        """Serialize a value to string, converting dicts/lists to JSON."""
        if isinstance(value, dict | list):
            return json.dumps(value)
        return str(value) if value is not None else ""

    for message in messages:
        row = [
            message.id,
            message.input.get("content", ""),
            message.output.get("content", ""),
        ]

        for key in context_keys:
            value = message.context.get(key, "") if message.context else ""
            row.append(_serialize_value(value))

        for key in participant_data_keys:
            value = message.participant_data.get(key, "") if message.participant_data else ""
            row.append(_serialize_value(value))

        for key in session_state_keys:
            value = message.session_state.get(key, "") if message.session_state else ""
            row.append(_serialize_value(value))

        row.append(message.full_history)
        writer.writerow(row)

    return response


@login_and_team_required
@require_POST
def upload_dataset_csv(request, team_slug: str, pk: int):
    """Upload CSV to update an existing dataset"""
    dataset = get_object_or_404(EvaluationDataset, id=pk, team__slug=team_slug)

    try:
        csv_file = request.FILES.get("csv_file")
        if not csv_file:
            return JsonResponse({"error": "No CSV file provided"}, status=400)

        # Save the CSV file to the Files model for processing
        file_instance = File.create(
            filename=csv_file.name,
            file_obj=csv_file,
            team_id=request.team.id,
            metadata={
                "upload_timestamp": timezone.now().isoformat(),
                "dataset_id": dataset.id,
            },
            purpose=FilePurpose.EVALUATION_DATASET,
            expiry_date=timezone.now() + timedelta(days=3),
        )

        task = update_dataset_from_csv_task.delay(dataset.id, file_instance.id, request.team.id)
        return JsonResponse({"success": True, "task_id": task.id})

    except Exception as e:
        logger.error(f"Error starting CSV upload for dataset {dataset.id}: {str(e)}")
        return JsonResponse({"error": "An error occurred while starting the CSV upload"}, status=500)


class AddMessageToDatasetView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "documents.change_documentsource"
    template_name = "experiments/components/add_to_dataset_modal.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["datasets"] = EvaluationDataset.objects.filter(team=self.request.team)
        context["session_id"] = self.kwargs.get("session_id")
        return context

    def post(self, request, team_slug: str, session_id: str):
        message_id = request.POST["message_id"]
        dataset = get_object_or_404(EvaluationDataset, id=request.POST["dataset"], team__slug=team_slug)

        if not ChatMessage.objects.filter(id=message_id, chat__experiment_session__team__slug=team_slug).exists():
            messages.error(request, "Invalid message selected.")
            return HttpResponse(status=400)

        eval_messages = make_evaluation_messages_from_sessions({str(session_id): [int(message_id)]})
        if not eval_messages:
            messages.error(request, "No valid messages found to add to dataset.")
            return HttpResponse(status=400)

        EvaluationMessage.objects.bulk_create(eval_messages)
        dataset.messages.add(*eval_messages)

        messages.success(request, "Messages added to dataset successfully.")
        return HttpResponse(status=204)
