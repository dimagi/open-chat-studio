"""Shared prefetch helpers for CustomTaggedItem on Chat-bearing rows.

Two flavours are exposed:

- ``chat_tagged_items_prefetch()`` — a ``Prefetch`` to attach to a queryset
  whose result is naturally bounded (one session, one analysis, one
  participant). Tag rendering (`Chat.prefetched_tags_json`) reads
  ``prefetched_tagged_items`` from each chat instance.

- ``attach_chat_tagged_items(rows)`` — page-bounded equivalent. Use in
  ``SingleTableView.get_table_data`` so the tag query scales with the
  visible page rather than the full filtered queryset.

Both populate ``row.chat.prefetched_tagged_items`` with the same shape, so
``Chat.prefetched_tags_json`` works identically afterwards.
"""

from django.contrib.contenttypes.models import ContentType
from django.db.models import Prefetch

from apps.annotations.models import CustomTaggedItem


def chat_tagged_items_prefetch() -> Prefetch:
    """Return the standard ``chat__tagged_items`` Prefetch with tag/user select_related."""
    return Prefetch(
        "chat__tagged_items",
        queryset=CustomTaggedItem.objects.select_related("tag", "user"),
        to_attr="prefetched_tagged_items",
    )


def attach_chat_tagged_items(rows):
    """Materialise ``rows`` and attach ``prefetched_tagged_items`` to each row's chat.

    A single ``CustomTaggedItem`` query keyed by chat_id replaces the
    queryset-level Prefetch, keeping the cost bounded by ``len(rows)``
    rather than the upstream filtered queryset.

    Accepts either model instances directly or django-tables2 ``BoundRow``
    wrappers (whose ``.record`` is the underlying instance) — different
    paginators expose ``page.object_list`` as different shapes, and callers
    shouldn't have to care which.

    The caller's queryset MUST ``select_related("chat")`` (or otherwise
    pre-populate ``row.chat``); this helper sets attributes on the related
    ``Chat`` instance, and a deferred FK would silently regress to N+1.
    """
    from apps.chat.models import Chat  # noqa: PLC0415

    records = [getattr(r, "record", r) for r in rows]
    if not records:
        return records
    chat_ids = [row.chat_id for row in records]
    tagged_by_chat: dict[int, list[CustomTaggedItem]] = {chat_id: [] for chat_id in chat_ids}
    chat_ct = ContentType.objects.get_for_model(Chat)
    for item in CustomTaggedItem.objects.filter(content_type=chat_ct, object_id__in=chat_ids).select_related(
        "tag", "user"
    ):
        tagged_by_chat[item.object_id].append(item)
    for row in records:
        row.chat.prefetched_tagged_items = tagged_by_chat.get(row.chat_id, [])
    return records
