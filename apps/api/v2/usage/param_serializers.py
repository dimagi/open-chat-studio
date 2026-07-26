"""Query-param validation for ``GET /api/v2/usage/``.

Kept separate from the response serializers (``serializers.py``) so the request contract and the
response contract evolve independently, mirroring ``apps/api/v2/inspect``. The view derives its
OpenAPI query parameters directly from this serializer, so there is a single source of truth for the
request contract. This serializer also resolves the reporting window: it turns ``period`` or an
explicit ``start``/``end`` into the ``[start, end)`` datetimes the service works in.
"""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone
from rest_framework import serializers

from apps.api.v2.usage.services import (
    GRANULARITY_CHOICES,
    GRANULARITY_TOTAL,
    GROUP_BY_CHOICES,
    GROUP_PARTICIPANT,
    METRIC_PARTICIPANTS,
    PERIOD_CHOICES,
    PERIOD_CURRENT_MONTH,
    SUPPORTED_METRICS,
    _month_bounds,
)
from apps.channels.models import ChannelPlatform

# Cap on how many buckets a single response may span, applied per granularity. It bounds both the
# response size and the aggregation cost, and is what rejects e.g. ``daily`` over a multi-year range.
MAX_BUCKETS = 366
# Rough bucket estimate: window length over the bucket size in days (monthly uses 28, the shortest
# month). It only needs to reject absurd ranges, so being off by the odd partial bucket at the
# window edges is immaterial against a cap of 366.
_DAYS_PER_BUCKET = {"daily": 1, "weekly": 7, "monthly": 28}


class _WindowDateTimeField(serializers.DateTimeField):
    """Accepts an ISO date (``2026-07-01``) or datetime. Timezone handling is deferred to the
    serializer's ``validate`` — naive values are localized to the request ``tz`` there — so this
    field leaves the parsed value untouched rather than forcing it to UTC as DRF would by default."""

    def __init__(self, **kwargs):
        kwargs.setdefault("input_formats", ["iso-8601", "%Y-%m-%d"])
        super().__init__(**kwargs)

    def enforce_timezone(self, value):
        return value


class UsageQuerySerializer(serializers.Serializer):
    # Repeat the param to request several metrics: ``?metric=messages&metric=sessions``. Returns a
    # set, so duplicates collapse. MultipleChoiceField reads repeated query params via ``getlist``.
    metric = serializers.MultipleChoiceField(
        choices=sorted(SUPPORTED_METRICS),
        allow_empty=False,
        help_text="Metrics to return; repeat the parameter for several.",
    )
    # No default: absence means "not supplied", which lets ``validate`` distinguish it from an
    # explicit ``start``/``end`` window and fall back to the current month only when neither is given.
    period = serializers.ChoiceField(
        choices=list(PERIOD_CHOICES),
        required=False,
        help_text=(
            "Calendar month to report on, evaluated in the given timezone. Mutually exclusive with "
            "'start'/'end'; defaults to the current month when no explicit window is given."
        ),
    )
    start = _WindowDateTimeField(
        required=False,
        help_text="Inclusive start of an explicit window (ISO date or datetime). Requires 'end'.",
    )
    end = _WindowDateTimeField(
        required=False,
        help_text="Exclusive end of an explicit window (ISO date or datetime). Requires 'start'.",
    )
    granularity = serializers.ChoiceField(
        choices=list(GRANULARITY_CHOICES),
        default=GRANULARITY_TOTAL,
        help_text=(
            "Time-bucketing of results. 'total' (default) returns a single object; the others return "
            "one row per bucket, each carrying 'bucket_start'."
        ),
    )
    group_by = serializers.ChoiceField(
        choices=list(GROUP_BY_CHOICES),
        required=False,
        help_text=(
            "Break the results down by this dimension: one row per group, cursor-paginated. Combines "
            "with 'granularity' to give one row per (group, time bucket)."
        ),
    )
    participant = serializers.UUIDField(
        required=False,
        help_text="Restrict to a single participant by their ``public_id``.",
    )
    participant_identifier = serializers.CharField(
        required=False,
        max_length=320,
        help_text="Restrict to a single participant by their raw identifier (email/phone).",
    )
    chatbot = serializers.UUIDField(
        required=False,
        help_text="Restrict to a single chatbot by its ``public_id``.",
    )
    platform = serializers.ChoiceField(
        # ``evaluations`` is excluded: the usage API drops evaluation-harness sessions, so filtering or
        # grouping by that platform would report messages while ``sessions`` stayed structurally zero.
        choices=[value for value in ChannelPlatform.values if value != ChannelPlatform.EVALUATIONS],
        required=False,
        help_text="Restrict to a single channel platform by its slug (e.g. 'web', 'whatsapp').",
    )
    tz = serializers.CharField(
        default="UTC",
        help_text="IANA timezone name defining calendar and bucket boundaries. Defaults to UTC.",
    )

    def validate_tz(self, value: str) -> ZoneInfo:
        try:
            return ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as err:
            raise serializers.ValidationError(f"Unknown timezone: {value}.") from err

    def validate(self, attrs: dict) -> dict:
        if attrs.get("participant") and attrs.get("participant_identifier"):
            raise serializers.ValidationError(
                "Provide only one of 'participant' or 'participant_identifier', not both."
            )
        if attrs.get("group_by") == GROUP_PARTICIPANT and METRIC_PARTICIPANTS in attrs["metric"]:
            # A per-participant breakdown makes the distinct-participant count trivially 1 per row.
            raise serializers.ValidationError(
                "The 'participants' metric is redundant with group_by=participant; drop one of them."
            )
        tz = attrs["tz"]
        start, end = self._resolve_window(attrs, tz)
        self._guard_window(start, end, attrs["granularity"])
        attrs["start"] = start
        attrs["end"] = end
        return attrs

    def _resolve_window(self, attrs: dict, tz: ZoneInfo) -> tuple[datetime, datetime]:
        period = attrs.get("period")
        start = _localize(attrs.get("start"), tz)
        end = _localize(attrs.get("end"), tz)
        if start is None and end is None:
            return _month_bounds(period or PERIOD_CURRENT_MONTH, tz)
        if period:
            raise serializers.ValidationError("Provide either 'period' or an explicit 'start'/'end' window, not both.")
        return _explicit_window(start, end)

    def _guard_window(self, start: datetime, end: datetime, granularity: str) -> None:
        # ``total`` is a single row regardless of window size, so window length is unconstrained there.
        if granularity == GRANULARITY_TOTAL:
            return
        span_days = (end - start).total_seconds() / 86400
        buckets = span_days / _DAYS_PER_BUCKET[granularity]
        if buckets > MAX_BUCKETS:
            raise serializers.ValidationError(
                f"The requested window is too large for '{granularity}' granularity "
                f"(it would exceed {MAX_BUCKETS} buckets). Use a coarser granularity or a smaller window."
            )


def _explicit_window(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    if start is None or end is None:
        raise serializers.ValidationError("Both 'start' and 'end' are required for an explicit window.")
    if end <= start:
        raise serializers.ValidationError("'end' must be after 'start'.")
    return start, end


def _localize(value: datetime | None, tz: ZoneInfo) -> datetime | None:
    """Interpret a naive ``start``/``end`` (a date or a datetime without offset) in the request ``tz``;
    leave an offset-aware value as the caller specified it."""
    if value is None:
        return None
    if timezone.is_naive(value):
        return value.replace(tzinfo=tz)
    return value
