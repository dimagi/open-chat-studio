"""Page-bounded prefetch of annotation queue membership for session rows."""

from collections.abc import Iterable
from typing import Any

from django.db.models.functions import Lower

from apps.human_annotations.models import AnnotationItem


def attach_session_annotation_items(rows: Iterable[Any]) -> list[Any]:
    """Attach each row's session-level annotation items, with their queue loaded, as `prefetched_annotation_items`.

    One query keyed by session id, so the cost follows the page size rather than the filtered
    queryset. Accepts model instances or django-tables2 BoundRows.
    """
    records = [getattr(row, "record", row) for row in rows]
    if not records:
        return records
    items_by_session: dict[int, list[AnnotationItem]] = {record.pk: [] for record in records}
    items = (
        AnnotationItem.objects.filter(session_id__in=items_by_session)
        .select_related("queue")
        .order_by(Lower("queue__name"))
    )
    for item in items:
        items_by_session[item.session_id].append(item)
    for record in records:
        record.prefetched_annotation_items = items_by_session[record.pk]
    return records
