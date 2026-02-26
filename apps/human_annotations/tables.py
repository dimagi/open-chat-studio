from django.conf import settings
from django.urls import reverse
from django_tables2 import TemplateColumn, columns, tables

from apps.experiments.models import ExperimentSession
from apps.generics import actions
from apps.generics.actions import chip_action
from apps.generics.tables import TemplateColumnWithCustomHeader
from apps.teams.utils import get_slug_for_team

from .models import AnnotationItem, AnnotationQueue


def _item_chip_url(_, request, record, ___):
    return reverse(
        "human_annotations:annotate_item",
        args=[request.team.slug, record.queue_id, record.pk],
    )


class AnnotationQueueTable(tables.Table):
    name = actions.ActionsColumn(
        actions=[
            chip_action(
                label_factory=lambda record, _: record.name,
                button_style="btn-soft btn-primary",
            ),
        ],
        align="left",
        orderable=True,
    )
    progress = columns.Column(verbose_name="Progress", empty_values=(), orderable=False)
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="human_annotations:queue_edit"),
            actions.delete_action(url_name="human_annotations:queue_delete"),
        ]
    )

    class Meta:
        model = AnnotationQueue
        fields = ["name", "status", "num_reviews_required", "progress", "created_at", "actions"]
        attrs = {"class": "table"}
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS

    def render_progress(self, record):
        # Use annotated fields from queryset if available, otherwise fall back to get_progress()
        if hasattr(record, "_total_items"):
            total_items = record._total_items
            reviews_done = record._reviews_done or 0
            total_needed = total_items * record.num_reviews_required
            percent = round((reviews_done / total_needed) * 100) if total_needed > 0 else 0
            return f"{reviews_done}/{total_needed} reviews ({percent}%)"
        progress = record.get_progress()
        return f"{progress['reviews_done']}/{progress['total_reviews_needed']} reviews ({progress['percent']}%)"


class AnnotationItemTable(tables.Table):
    description = actions.ActionsColumn(
        actions=[
            chip_action(
                label_factory=lambda record, _: str(record),
                url_factory=_item_chip_url,
                button_style="btn-soft btn-secondary max-w-xs truncate",
            ),
        ],
        align="left",
        verbose_name="Item",
    )
    item_type = columns.Column(verbose_name="Type")
    status = TemplateColumn(
        template_name="human_annotations/columns/item_status.html",
        verbose_name="Status",
        orderable=False,
    )
    review_count = columns.Column(verbose_name="Reviews")
    annotations_summary = TemplateColumn(
        template_name="human_annotations/columns/annotations_summary.html",
        verbose_name="Annotations",
        orderable=False,
    )

    class Meta:
        model = AnnotationItem
        fields = ["description", "item_type", "status", "review_count", "annotations_summary", "created_at"]
        attrs = {"class": "table"}
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS

    def render_review_count(self, record):
        return f"{record.review_count}/{record.queue.num_reviews_required}"


def _annotation_session_url_factory(_, request, record, __):
    return reverse(
        "chatbots:chatbot_session_view",
        args=[get_slug_for_team(record.team_id), record.experiment.public_id, record.external_id],
    )


class AnnotationSessionsSelectionTable(tables.Table):
    selection = TemplateColumnWithCustomHeader(
        template_name="evaluations/session_checkbox.html",
        verbose_name="Select",
        orderable=False,
        extra_context={
            "css_class": "checkbox checkbox-primary session-checkbox",
            "js_function": "updateSelectedSessions()",
        },
        header_template="evaluations/session_checkbox.html",
        header_context={
            "help_content": "Select all sessions on this page",
            "js_function": "toggleSelectedSessions()",
            "css_class": "checkbox checkbox-primary session-checkbox",
        },
    )
    experiment = columns.Column(accessor="experiment", verbose_name="Experiment")
    participant = columns.Column(accessor="participant", verbose_name="Participant")
    last_message = columns.Column(accessor="last_activity_at", verbose_name="Last Message", orderable=True)
    message_count = columns.Column(accessor="message_count", verbose_name="Messages", orderable=False)
    session = actions.ActionsColumn(
        actions=[
            chip_action(
                label="View Session",
                url_factory=_annotation_session_url_factory,
                open_url_in_new_tab=True,
            ),
        ],
        orderable=False,
    )

    class Meta:
        model = ExperimentSession
        fields = []
        row_attrs = {
            **settings.DJANGO_TABLES2_ROW_ATTRS,
            "data-redirect-target": "_blank",
        }
        attrs = {"class": "table w-full"}
        orderable = False
        empty_text = "No sessions available."
