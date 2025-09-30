from django.conf import settings
from django.urls import reverse
from django.utils.safestring import mark_safe
from django_tables2 import TemplateColumn, columns, tables

from apps.evaluations.models import (
    EvaluationConfig,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationRun,
    Evaluator,
    ExperimentVersionSelection,
)
from apps.evaluations.utils import get_evaluator_type_display
from apps.experiments.models import ExperimentSession
from apps.generics import actions


class EvaluationConfigTable(tables.Table):
    name = columns.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    generation_chatbot = columns.Column(
        verbose_name="Generation Chatbot",
        orderable=False,
        empty_values=(),  # Don't show "—" for empty values, let render method handle it
    )
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="evaluations:edit"),
            actions.Action(
                url_name="evaluations:create_evaluation_preview",
                url_factory=lambda url_name, request, record, value: reverse(
                    url_name, args=[request.team.slug, record.id]
                ),
                icon_class="fa-solid fa-eye",
                title="Preview",
            ),
            actions.Action(
                url_name="evaluations:create_evaluation_run",
                url_factory=lambda url_name, request, record, value: reverse(
                    url_name, args=[request.team.slug, record.id]
                ),
                icon_class="fa-solid fa-play",
                title="Run",
            ),
        ]
    )

    def render_evaluators(self, value, record):
        """Render the evaluators column with icons and labels in an unordered list."""
        from apps.evaluations.utils import get_evaluator_type_display

        if not value.exists():
            return "—"

        items = []
        for evaluator in value.all():
            type_info = get_evaluator_type_display(evaluator.type)
            icon_html = f'<i class="fa {type_info["icon"]}"></i> ' if type_info["icon"] else ""
            items.append(f"<li>{icon_html}{evaluator.name} ({type_info['label']})</li>")

        return mark_safe(f'<ul class="list-disc list-inside">{"".join(items)}</ul>')

    def render_generation_chatbot(self, record):
        if not record.base_experiment:
            return "—"
        if record.version_selection_type == ExperimentVersionSelection.LATEST_WORKING:
            return f"{record.base_experiment.name} (Latest Working)"
        elif record.version_selection_type == ExperimentVersionSelection.LATEST_PUBLISHED:
            return f"{record.base_experiment.name} (Latest Published)"
        elif record.experiment_version:
            version_display = (
                f" ({record.experiment_version.version_display})" if record.experiment_version.version_display else ""
            )
            return f"{record.experiment_version.name}{version_display}"
        return "—"

    class Meta:
        model = EvaluationConfig
        fields = (
            "name",
            "evaluators",
            "dataset",
            "generation_chatbot",
            "actions",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No evaluation configurations found."


class EvaluationRunTable(tables.Table):
    created_at = columns.DateTimeColumn(
        verbose_name="Created",
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )

    status = TemplateColumn(
        template_name="evaluations/evaluation_run_status_column.html", verbose_name="Status", orderable=False
    )

    results = columns.Column(accessor="results__count", verbose_name="Result count", orderable=False)

    actions = actions.ActionsColumn(
        actions=[
            actions.Action(
                url_name="evaluations:evaluation_run_download",
                url_factory=lambda url_name, request, record, _: reverse(
                    url_name, args=[request.team.slug, record.config_id, record.id]
                ),
                icon_class="fa-solid fa-download",
                title="Download CSV",
                enabled_condition=lambda _, record: record.status == "completed",
            ),
        ]
    )

    class Meta:
        model = EvaluationRun
        fields = ("created_at", "status", "finished_at", "results", "actions")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No runs found."


class EvaluatorTable(tables.Table):
    name = columns.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    type = columns.Column(
        verbose_name="Type",
        orderable=True,
    )
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="evaluations:evaluator_edit"),
            actions.AjaxAction(
                "evaluations:evaluator_delete",
                title="Delete",
                icon_class="fa-solid fa-trash",
                confirm_message="This will permanently delete the evaluator. Are you sure?",
                hx_method="delete",
            ),
        ]
    )

    def render_type(self, value, record):
        """Render the type column with icon and label."""
        type_info = get_evaluator_type_display(value)
        icon_html = f'<i class="fa {type_info["icon"]}"></i> ' if type_info["icon"] else ""
        return mark_safe(f"{icon_html}{type_info['label']}")

    class Meta:
        model = Evaluator
        fields = (
            "name",
            "type",
            "actions",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No evaluators found."


class EvaluationDatasetTable(tables.Table):
    name = columns.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    message_count = columns.Column(accessor="message_count", verbose_name="Messages", orderable=False)
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="evaluations:dataset_edit"),
            actions.Action(
                url_name="evaluations:dataset_download",
                url_factory=lambda url_name, request, record, _: reverse(url_name, args=[request.team.slug, record.id]),
                icon_class="fa-solid fa-download",
                title="Download CSV",
            ),
            actions.AjaxAction(
                "evaluations:dataset_delete",
                title="Delete",
                icon_class="fa-solid fa-trash",
                confirm_message="This will permanently delete the dataset and all its messages. Are you sure?",
                hx_method="delete",
            ),
        ]
    )

    class Meta:
        model = EvaluationDataset
        fields = (
            "name",
            "message_count",
            "actions",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No datasets found."


class EvaluationSessionsSelectionTable(tables.Table):
    selection = columns.CheckBoxColumn(
        accessor="external_id",
        verbose_name="Select",
        orderable=False,
        attrs={
            "input": {"class": "checkbox checkbox-primary session-checkbox", "@change": "updateSelectedSessions()"},
            "th__input": {"style": "display: none;"},  # Hide the select all checkbox in header
        },
    )
    experiment = columns.Column(accessor="experiment", verbose_name="Experiment", order_by="experiment__name")
    participant = columns.Column(accessor="participant", verbose_name="Participant", order_by="participant__identifier")
    last_message = columns.Column(accessor="last_message_created_at", verbose_name="Last Message", orderable=True)
    versions = columns.Column(verbose_name="Versions", accessor="experiment_version_for_display", orderable=False)
    message_count = columns.Column(accessor="message_count", verbose_name="Messages", orderable=False)

    class Meta:
        model = ExperimentSession
        fields = []
        row_attrs = {
            **settings.DJANGO_TABLES2_ROW_ATTRS,
            "data-redirect-url": None,
        }
        orderable = False
        empty_text = "No sessions available for selection."


class DatasetMessagesTable(tables.Table):
    human_message_content = TemplateColumn(
        template_name="evaluations/dataset_message_human_column.html",
        verbose_name="Human Message",
        orderable=False,
    )
    ai_message_content = TemplateColumn(
        template_name="evaluations/dataset_message_ai_column.html",
        verbose_name="AI Message",
        orderable=False,
    )
    context = TemplateColumn(
        template_name="evaluations/dataset_message_context_column.html",
        verbose_name="Context",
        orderable=False,
        extra_context={"field": "context"},
    )
    history = TemplateColumn(
        template_name="evaluations/dataset_message_history_column.html",
        verbose_name="History",
        orderable=False,
    )
    participant_data = TemplateColumn(
        template_name="evaluations/dataset_message_context_column.html",
        verbose_name="Participant Data",
        orderable=False,
        extra_context={"field": "participant_data", "field_verbose_name": "Participant Data"},
    )
    session_state = TemplateColumn(
        template_name="evaluations/dataset_message_context_column.html",
        verbose_name="Session State",
        orderable=False,
        extra_context={"field": "session_state", "field_verbose_name": "Session State"},
    )
    source = TemplateColumn(
        template_name="evaluations/dataset_message_source_column.html",
        verbose_name="Source",
        orderable=False,
    )
    actions = actions.ActionsColumn(
        actions=[
            actions.Action(
                url_name="evaluations:edit_message_modal",
                template="evaluations/dataset_message_edit_action.html",
            ),
            actions.AjaxAction(
                url_name="evaluations:delete_message",
                url_factory=lambda url_name, request, record, value: reverse(
                    url_name, args=[request.team.slug, record.id]
                ),
                icon_class="fa-solid fa-trash",
                title="Delete message",
                button_style="btn btn-sm",
                confirm_message="Are you sure you want to delete this message? This action cannot be undone.",
                hx_method="delete",
            ),
        ]
    )

    class Meta:
        model = EvaluationMessage
        fields = (
            "source",
            "human_message_content",
            "ai_message_content",
            "context",
            "history",
            "participant_data",
            "session_state",
            "actions",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No messages in this dataset yet."
