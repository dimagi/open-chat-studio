from django.conf import settings
from django.urls import reverse
from django_tables2 import TemplateColumn, columns, tables

from apps.evaluations.models import EvaluationConfig, EvaluationDataset, EvaluationRun, Evaluator
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

    class Meta:
        model = Evaluator
        fields = (
            "name",
            "actions",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No evaluators found."


class EvaluationDatasetTable(tables.Table):
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="evaluations:dataset_edit"),
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
        model = EvaluationDataset
        fields = (
            "name",
            "messages",
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
