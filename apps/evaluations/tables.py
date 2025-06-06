import inspect

from django.conf import settings
from django.urls import reverse
from django.utils.safestring import mark_safe
from django_tables2 import TemplateColumn, columns, tables

from apps.evaluations import evaluators
from apps.evaluations.models import EvaluationConfig, EvaluationDataset, EvaluationMessage, EvaluationRun, Evaluator
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
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="evaluations:edit"),
            actions.Action(
                url_name="evaluations:create_evaluation_run",
                url_factory=lambda url_name, request, record, value: reverse(
                    url_name, args=[request.team.slug, record.id]
                ),
                icon_class="fa-solid fa-play",
                title="Run",
            ),
        ]
        # actions=[
        #     actions.edit_action(url_name="pipelines:edit"),
        #     actions.AjaxAction(
        #         "pipelines:delete",
        #         title="Archive",
        #         icon_class="fa-solid fa-box-archive",
        #         required_permissions=["pipelines.delete_pipeline"],
        #         confirm_message="This will delete the pipeline and any associated logs. Are you sure?",
        #         hx_method="delete",
        #     ),
        # ]
    )

    class Meta:
        model = EvaluationConfig
        fields = (
            "name",
            "evaluators",
            "dataset",
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

    results = columns.Column(accessor="results.count", verbose_name="Result count", orderable=False)
    # actions = actions.chip_column(label="Session Details")

    class Meta:
        model = EvaluationRun
        fields = ("created_at", "finished_at", "results")
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
            # actions.edit_action(url_name="evaluations:edit"),
        ]
        # actions=[
        #     actions.edit_action(url_name="pipelines:edit"),
        #     actions.AjaxAction(
        #         "pipelines:delete",
        #         title="Archive",
        #         icon_class="fa-solid fa-box-archive",
        #         required_permissions=["pipelines.delete_pipeline"],
        #         confirm_message="This will delete the pipeline and any associated logs. Are you sure?",
        #         hx_method="delete",
        #     ),
        # ]
    )

    def render_type(self, value, record):
        """Render the type column with icon and label."""
        evaluator_classes = [
            cls
            for _, cls in inspect.getmembers(evaluators, inspect.isclass)
            if issubclass(cls, evaluators.BaseEvaluator) and cls != evaluators.BaseEvaluator
        ]
        evaluator_class = None
        for cls in evaluator_classes:
            if cls.__name__ == value:
                evaluator_class = cls
                break
        if evaluator_class:
            evaluator_schema = evaluator_class.model_config.get("evaluator_schema")
            if evaluator_schema:
                icon_html = f'<i class="fa {evaluator_schema.icon}"></i> ' if evaluator_schema.icon else ""
                return mark_safe(f"{icon_html}{evaluator_schema.label}")
        return value

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


class EvaluationSessionsTable(tables.Table):
    experiment = columns.Column(accessor="experiment", verbose_name="Experiment", order_by="experiment__name")
    participant = columns.Column(accessor="participant", verbose_name="Participant", order_by="participant__identifier")
    last_message = columns.Column(accessor="last_message_created_at", verbose_name="Last Message", orderable=True)
    versions = columns.Column(verbose_name="Versions", accessor="experiment_version_for_display", orderable=False)
    clone = TemplateColumn(
        template_name="evaluations/show_messages_modal_action.html", verbose_name="", orderable=False
    )

    class Meta:
        model = ExperimentSession
        fields = []
        row_attrs = {
            **settings.DJANGO_TABLES2_ROW_ATTRS,
            "data-redirect-url": None,
        }
        orderable = False
        empty_text = "No sessions yet!"


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
    message_count = columns.Column(accessor="chat.messages.count", verbose_name="Messages", orderable=False)

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
    )
    source = TemplateColumn(
        template_name="evaluations/dataset_message_source_column.html",
        verbose_name="Source",
        orderable=False,
    )

    class Meta:
        model = EvaluationMessage
        fields = ("human_message_content", "ai_message_content", "context", "source")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No messages in this dataset yet."
