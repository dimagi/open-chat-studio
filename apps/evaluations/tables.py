from django.conf import settings
from django.urls import reverse
from django_tables2 import columns, tables

from apps.evaluations.models import EvaluationConfig, EvaluationDataset, EvaluationRun, Evaluator
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
            "message_type",
            "sessions",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No datasets found."
