from django.conf import settings
from django_tables2 import columns, tables

from apps.generics import actions
from apps.pipelines.models import Pipeline, PipelineRun


class PipelineTable(tables.Table):
    name = columns.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        extra_context={
            "actions": [
                actions.edit_action(url_name="pipelines:edit"),
                actions.delete_action(
                    url_name="pipelines:delete",
                    confirm_message="This will delete the pipeline and any associated logs. Are you sure?",
                ),
            ]
        },
    )
    runs = columns.Column(accessor="run_count")

    class Meta:
        model = Pipeline
        fields = (
            "name",
            "runs",
            "actions",
        )
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No pipelines found."


class PipelineRunTable(tables.Table):
    created_at = columns.DateTimeColumn(
        verbose_name="Created",
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )

    class Meta:
        model = PipelineRun
        fields = ("created_at", "status")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No runs found."
