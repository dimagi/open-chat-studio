from django.conf import settings
from django.urls import reverse
from django.utils.safestring import mark_safe
from django_tables2 import TemplateColumn, columns, tables

from apps.evaluations.models import EvaluationConfig, EvaluationDataset, EvaluationMessage, EvaluationRun, Evaluator
from apps.evaluations.utils import get_evaluator_type_display
from apps.experiments.models import ExperimentSession
from apps.generics import actions


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
    human_message_content = columns.Column(
        accessor="input.content",
        verbose_name="Human Message",
        orderable=False,
    )
    ai_message_content = columns.Column(
        accessor="output.content",
        verbose_name="AI Message",
        orderable=False,
    )
    context = TemplateColumn(
        template_name="evaluations/dataset_message_context_column.html",
        verbose_name="Context",
        orderable=False,
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
        fields = ("human_message_content", "ai_message_content", "context", "source", "actions")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No messages in this dataset yet."
