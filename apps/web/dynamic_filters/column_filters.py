from datetime import datetime, timedelta

import pytz
from django.db.models import Q, QuerySet

from apps.experiments.models import Experiment, SessionStatus

from .base import DATE_RANGE_OPTIONS, TYPE_TIMESTAMP, ChoiceColumnFilter, ColumnFilter, StringColumnFilter


class ParticipantFilter(StringColumnFilter):
    query_param: str = "participant"
    columns: list[str] = ["participant__identifier", "participant__name"]
    label: str = "Participant"
    description: str = "Filter by participant name or identifier"


class ExperimentFilter(ChoiceColumnFilter):
    query_param: str = "experiment"
    column: str = "experiment_id"
    label: str = "Chatbot"
    description: str = (
        "Filter by chatbot. Values are numeric database IDs — call get_filter_options('experiment') "
        "to look up the ID for a chatbot name. Do NOT use the chatbot name string as a value."
    )

    def prepare(self, team, **_):
        experiments = (
            Experiment.objects.working_versions_queryset().filter(team=team).values("id", "name").order_by("name")
        )
        self.options = [{"id": exp["id"], "label": exp["name"]} for exp in experiments]

    def parse_query_value(self, value) -> list[int]:  # ty: ignore[invalid-method-override]
        values = []
        for v in self.values_list(value):
            try:
                values.append(int(v))
            except (ValueError, TypeError):
                continue
        return values

    def _get_filter_clause(self, id_values):
        # This experiment and all of its versions should be returned
        return Q(experiment_id__in=id_values) | Q(experiment__working_version_id__in=id_values)

    def apply_any_of(self, queryset, value, timezone=None) -> QuerySet:
        return queryset.filter(self._get_filter_clause(value))

    def apply_all_of(self, queryset, value, timezone=None) -> QuerySet:
        for val in value:
            queryset = queryset.filter(self._get_filter_clause(val))
        return queryset

    def apply_excludes(self, queryset, value, timezone=None) -> QuerySet:
        return queryset.exclude(self._get_filter_clause(value))


class SessionStatusFilter(ChoiceColumnFilter):
    column: str = "status"
    label: str = "Status"
    options: list[str | dict] = [{"id": value, "label": label} for value, label in SessionStatus.choices]
    description: str = "Filter by session status (e.g. active, complete, pending-review)"


class RemoteIdFilter(ChoiceColumnFilter):
    query_param: str = "remote_id"
    column: str = "participant__remote_id"
    label: str = "Remote ID"
    description: str = "Filter by participant's remote/external ID"


class SessionIdFilter(StringColumnFilter):
    query_param: str = "session_id"
    columns: list[str] = ["external_id"]
    label: str = "Session ID"
    description: str = "Filter by the session's external ID (UUID)"

    def apply_equals(self, queryset, value, timezone=None) -> QuerySet:
        return self._apply_with_lookup(queryset, "iexact", value)


class TimestampFilter(ColumnFilter):
    type: str = TYPE_TIMESTAMP
    options: list[dict[str, str]] = DATE_RANGE_OPTIONS
    description: str = "Filter by date/time"

    def _get_date_as_utc(self, value) -> datetime | None:
        try:
            date_value = datetime.fromisoformat(value)
            # Convert date to UTC to compare it correctly with stored timestamps
            return date_value.astimezone(pytz.UTC)
        except (ValueError, TypeError, pytz.UnknownTimeZoneError):
            # Filter values come from URL query params controlled by the user; an
            # invalid value should silently no-op (return the unfiltered queryset)
            # rather than 500 the page or spam logs.
            return None

    def _filter_by_lookup(self, queryset, lookup_suffix: str, value):
        """Apply ``value`` with the given lookup suffix (e.g. ``"date__lt"``) on ``self.column``.

        Subclasses override to filter via an EXISTS subquery instead of joining
        through ``self.column`` directly.
        """
        return queryset.filter(**{f"{self.column}__{lookup_suffix}": value})

    def apply_on(self, queryset, value, timezone=None) -> QuerySet:
        """Filter for timestamps on a specific date"""
        if date_value := self._get_date_as_utc(value):
            return self._filter_by_lookup(queryset, "date", date_value)
        return queryset

    def apply_before(self, queryset, value, timezone=None) -> QuerySet:
        """Filter for timestamps before a specific date"""
        if date_value := self._get_date_as_utc(value):
            return self._filter_by_lookup(queryset, "date__lt", date_value)
        return queryset

    def apply_after(self, queryset, value, timezone=None) -> QuerySet:
        """Filter for timestamps after a specific date"""
        if date_value := self._get_date_as_utc(value):
            return self._filter_by_lookup(queryset, "date__gt", date_value)
        return queryset

    def apply_range(self, queryset, value, timezone=None) -> QuerySet:
        """Filter for relative time ranges like '1h', '7d'.
        For 1d 24h are subtracted i.e sessions in the range of 24h are shown not based on the date"""
        try:
            client_tz = pytz.timezone(timezone) if timezone else pytz.UTC
            now_client = datetime.now(client_tz)

            if not value.endswith(("h", "d", "m")):
                return queryset

            num = int(value[:-1])
            unit = value[-1]

            if unit == "h":
                delta = timedelta(hours=num)
            elif unit == "d":
                delta = timedelta(days=num)
            elif unit == "m":
                delta = timedelta(minutes=num)
            else:
                return queryset

            range_starting_utc_time = (now_client - delta).astimezone(pytz.UTC)
            return self._filter_by_lookup(queryset, "gte", range_starting_utc_time)
        except (ValueError, TypeError, pytz.UnknownTimeZoneError):
            # User-controlled URL value; silent no-op is preferred over a 500.
            return queryset
