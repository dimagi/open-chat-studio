from django.conf import settings
from django_tables2 import columns, tables

from apps.analysis.models import Analysis, AnalysisRun, RunGroup, RunStatus
from apps.generics import table_actions


class AnalysisTable(tables.Table):
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
                table_actions.Action(
                    "analysis:create_run",
                    "fa-solid fa-play",
                    required_permissions=["analysis.add_analysisrun"],
                    enabled_condition=lambda request, record: not record.needs_configuration(),
                ),
                table_actions.edit_action(
                    "analysis:edit",
                    required_permissions=["analysis.change_analysis"],
                ),
                table_actions.delete_action(
                    "analysis:delete",
                    required_permissions=["analysis.delete_analysis"],
                ),
            ]
        },
    )

    class Meta:
        model = Analysis
        fields = ("name",)
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No pipelines found."


def get_run_group_row_attrs():
    class_attr = settings.DJANGO_TABLES2_ROW_ATTRS["class"]
    class_attr_error = class_attr + " bg-error text-error-content"
    attrs = {
        **settings.DJANGO_TABLES2_ROW_ATTRS,
        "class": lambda record: class_attr_error if record.status == RunStatus.ERROR else "",
    }
    return attrs


class RunGroupTable(tables.Table):
    created_at = columns.DateTimeColumn(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    duration = columns.Column(
        accessor="duration_display",
        verbose_name="Duration",
        orderable=False,
    )
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        extra_context={
            "actions": [
                table_actions.Action(
                    "analysis:replay_run",
                    "fa-solid fa-play",
                    required_permissions=["analysis.add_rungroup"],
                ),
                table_actions.delete_action("analysis:delete_group", required_permissions=["analysis.delete_rungroup"]),
            ]
        },
    )

    class Meta:
        model = RunGroup
        fields = ("created_at", "status", "start_time")
        row_attrs = get_run_group_row_attrs()
        orderable = False
        empty_text = "No runs found."
