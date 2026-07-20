"""Query-param validation for ``GET /api/v2/usage/``.

Kept separate from the response serializers (``serializers.py``) so the request contract and the
response contract evolve independently, mirroring ``apps/api/v2/inspect``. The view derives its
OpenAPI query parameters directly from this serializer, so there is a single source of truth for the
request contract.
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rest_framework import serializers

from apps.api.v2.usage.services import PERIOD_CHOICES, PERIOD_CURRENT_MONTH, SUPPORTED_METRICS


class CommaSeparatedChoiceField(serializers.CharField):
    """A query param accepting a comma-separated list of values drawn from a fixed set. Returns the
    parsed list with duplicates removed and order preserved."""

    def __init__(self, *, choices, **kwargs):
        self._choices = frozenset(choices)
        super().__init__(**kwargs)

    def to_internal_value(self, data) -> list[str]:
        raw = super().to_internal_value(data)
        items = [item.strip() for item in raw.split(",") if item.strip()]
        if not items:
            raise serializers.ValidationError("At least one value is required.")
        unknown = [item for item in items if item not in self._choices]
        if unknown:
            supported = ", ".join(sorted(self._choices))
            raise serializers.ValidationError(f"Unknown value(s): {', '.join(unknown)}. Supported: {supported}.")
        return list(dict.fromkeys(items))


class UsageQuerySerializer(serializers.Serializer):
    metric = CommaSeparatedChoiceField(
        choices=SUPPORTED_METRICS,
        help_text=f"Comma-separated list of metrics to return. Supported: {', '.join(sorted(SUPPORTED_METRICS))}.",
    )
    period = serializers.ChoiceField(
        choices=list(PERIOD_CHOICES),
        default=PERIOD_CURRENT_MONTH,
        help_text="Calendar month to report on, evaluated in the given timezone.",
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
    tz = serializers.CharField(
        default="UTC",
        help_text="IANA timezone name defining calendar-month boundaries. Defaults to UTC.",
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
        return attrs
