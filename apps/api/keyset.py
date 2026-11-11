"""Exact ``(updated_at, id)`` keyset pagination, shared by the team-export and v2 sync APIs.

The cursor is a base64-wrapped JSON ``{"updated_at": ..., "id": ...}`` naming the last row a client has
seen. Ordering by both columns and filtering past both makes a page boundary inside a run of rows that
share one ``updated_at`` exact: nothing is skipped or repeated.
"""

import base64
import json
from datetime import datetime

from django.db.models import Q, QuerySet
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError


def encode_cursor(row) -> str:
    """The cursor that resumes after ``row``."""
    keyset = {"updated_at": row.updated_at.isoformat(), "id": row.id}
    return base64.b64encode(json.dumps(keyset).encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        keyset = json.loads(base64.b64decode(cursor))
        updated_at = parse_datetime(keyset["updated_at"])
        row_id = int(keyset["id"])
    except (ValueError, TypeError, KeyError) as e:
        raise ValidationError("Invalid pagination cursor.") from e
    if updated_at is None:
        raise ValidationError("Invalid pagination cursor.")
    return updated_at, row_id


def keyset_page(queryset: QuerySet, cursor: str | None, limit: int) -> tuple[list, bool]:
    """The rows after ``cursor`` in ``(updated_at, id)`` order, at most ``limit``, and whether more follow."""
    queryset = queryset.order_by("updated_at", "id")
    if cursor:
        updated_at, row_id = decode_cursor(cursor)
        queryset = queryset.filter(Q(updated_at__gt=updated_at) | Q(updated_at=updated_at, id__gt=row_id))
    rows = list(queryset[: limit + 1])
    return rows[:limit], len(rows) > limit
