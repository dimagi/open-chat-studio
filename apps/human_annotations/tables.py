import django_tables2 as tables
from django.middleware.csrf import get_token
from django.urls import reverse
from django.utils.html import format_html

from apps.generics import actions
from apps.generics.actions import chip_action

from .models import AnnotationItem, AnnotationQueue, AnnotationSchema


def _schema_chip_url(_, __, record, ___):
    return record.get_absolute_url()


def _queue_chip_url(_, __, record, ___):
    return record.get_absolute_url()


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
                url_factory=_schema_chip_url,
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

    def render_field_count(self, record):
        return len(record.schema)


class AnnotationQueueTable(tables.Table):
    name = actions.ActionsColumn(
        actions=[
            chip_action(
                label_factory=lambda record, _: record.name,
                url_factory=_queue_chip_url,
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

    def render_progress(self, record):
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
    status = tables.Column(empty_values=())
    review_count = tables.Column(verbose_name="Reviews")
    annotations_summary = tables.Column(verbose_name="Annotations", empty_values=(), orderable=False)

    class Meta:
        model = AnnotationItem
        fields = ["description", "item_type", "status", "review_count", "annotations_summary", "created_at"]
        attrs = {"class": "table"}

    _STATUS_BADGE = {
        "pending": "badge-ghost",
        "in_progress": "badge-info",
        "completed": "badge-success",
        "flagged": "badge-warning",
    }

    def render_status(self, record):
        badge_class = self._STATUS_BADGE.get(record.status, "badge-ghost")
        label = record.get_status_display()
        if record.status == "flagged":
            flags = record.flags or []
            tip_lines = []
            for flag in flags:
                line = flag.get("user", "Unknown")
                if flag.get("reason"):
                    line += f": {flag['reason']}"
                tip_lines.append(line)
            if tip_lines:
                badge = format_html(
                    '<span class="badge badge-soft {} tooltip tooltip-bottom" data-tip="{}">{}</span>',
                    badge_class,
                    " / ".join(tip_lines),
                    label,
                )
            else:
                badge = format_html('<span class="badge badge-soft {}">{}</span>', badge_class, label)
            unflag_url = reverse(
                "human_annotations:unflag_item",
                args=[self.request.team.slug, record.queue_id, record.pk],
            )
            csrf = get_token(self.request)
            return format_html(
                '<div class="flex items-center gap-1">{}'
                '<form method="post" action="{}" class="inline">'
                '<input type="hidden" name="csrfmiddlewaretoken" value="{}">'
                '<button type="submit" class="btn btn-ghost btn-xs">unflag</button>'
                "</form></div>",
                badge,
                unflag_url,
                csrf,
            )
        return format_html('<span class="badge badge-soft {}">{}</span>', badge_class, label)

    def render_review_count(self, record):
        return f"{record.review_count}/{record.queue.num_reviews_required}"

    def render_annotations_summary(self, record):
        submitted = record.annotations.filter(status="submitted").select_related("reviewer")
        if not submitted.exists():
            return format_html('<span class="text-gray-400 text-xs">{}</span>', "No annotations")

        parts = []
        for ann in submitted[:3]:
            reviewer_name = ann.reviewer.get_full_name() or ann.reviewer.username
            data_preview = ", ".join(f"{k}: {v}" for k, v in list(ann.data.items())[:3])
            parts.append(
                format_html(
                    '<div class="text-xs"><span class="font-medium">{}</span>: {}</div>',
                    reviewer_name,
                    data_preview,
                )
            )
        if submitted.count() > 3:
            parts.append(format_html('<div class="text-xs text-gray-400">+{} more</div>', submitted.count() - 3))
        return format_html("".join(str(p) for p in parts))
