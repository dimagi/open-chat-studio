from django.conf import settings
from django_tables2 import columns, tables

from apps.analysis.models import Analysis, RunGroup, RunStatus
from apps.generics import actions


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
                actions.Action(
                    "analysis:create_run",
                    icon_class="fa-solid fa-play",
                    required_permissions=["analysis.add_analysisrun"],
                    enabled_condition=lambda request, record: not record.needs_configuration(),
                ),
                actions.edit_action(
                    "analysis:edit",
                    required_permissions=["analysis.change_analysis"],
                ),
                actions.delete_action(
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

    def _get_class(record):
        # if you change these styles, also change them in settings.py (see DJANGO_TABLES2_ROW_ATTRS)
        match record.status:
            case RunStatus.ERROR:
                return class_attr + " text-error"
            case RunStatus.CANCELLED | RunStatus.CANCELLING:
                return class_attr + " text-neutral"
        return class_attr

    attrs = {
        **settings.DJANGO_TABLES2_ROW_ATTRS,
        "class": _get_class,
    }
    return attrs


class RunGroupTable(tables.Table):
    created_at = columns.DateTimeColumn(
        verbose_name="Created",
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
    feedback = columns.TemplateColumn(
        template_name="analysis/components/group_feedback.html",
        orderable=False,
        attrs={"th": {"class": "text-center"}, "td": {"class": "text-center"}},
    )
    actions = columns.TemplateColumn(
        template_name="generic/crud_actions_column.html",
        attrs={"th": {"class": "text-center"}, "td": {"class": "text-center"}},
        extra_context={
            "actions": [
                actions.Action(
                    "analysis:replay_run",
                    icon_class="fa-solid fa-arrow-rotate-left",
                    required_permissions=["analysis.add_rungroup"],
                ),
                actions.delete_action("analysis:delete_group", required_permissions=["analysis.delete_rungroup"]),
            ]
        },
    )

    class Meta:
        model = RunGroup
        fields = ("created_at", "created_by__get_display_name", "status")
        row_attrs = get_run_group_row_attrs()
        orderable = False
        empty_text = "No runs found."
