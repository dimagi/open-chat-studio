import django_tables2 as tables
from django.conf import settings
from django.urls import reverse

from apps.generics import actions
from apps.generics.actions import chip_action

from .models import AnnotationItem, AnnotationQueue, AnnotationSchema


def _item_chip_url(_, request, record, ___):
    return reverse(
        "human_annotations:annotate_item",
        args=[request.team.slug, record.queue_id, record.pk],
    )


class AnnotationSchemaTable(tables.Table):
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
    field_count = tables.Column(verbose_name="Fields", empty_values=(), orderable=False)
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="human_annotations:schema_edit"),
            actions.delete_action(url_name="human_annotations:schema_delete"),
        ]
    )

    class Meta:
        model = AnnotationSchema
        fields = ["name", "description", "field_count", "created_at", "actions"]
        attrs = {"class": "table"}
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS

    def render_field_count(self, record):
        return len(record.schema)


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
    progress = tables.Column(verbose_name="Progress", empty_values=(), orderable=False)
    actions = actions.ActionsColumn(
        actions=[
            actions.edit_action(url_name="human_annotations:queue_edit"),
            actions.delete_action(url_name="human_annotations:queue_delete"),
        ]
    )

    class Meta:
        model = AnnotationQueue
        fields = ["name", "schema", "status", "num_reviews_required", "progress", "created_at", "actions"]
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
                button_style="btn-soft btn-secondary",
            ),
        ],
        align="left",
        verbose_name="Item",
    )
    item_type = tables.Column(verbose_name="Type")
    status = tables.TemplateColumn(
        template_name="human_annotations/columns/item_status.html",
        verbose_name="Status",
        orderable=False,
    )
    review_count = tables.Column(verbose_name="Reviews")
    annotations_summary = tables.TemplateColumn(
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
