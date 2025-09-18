from datetime import datetime, timedelta

import pytz
from django.db.models import QuerySet

from apps.experiments.models import SessionStatus

from .base import DATE_RANGE_OPTIONS, TYPE_TIMESTAMP, ChoiceColumnFilter, ColumnFilter, StringColumnFilter


class ParticipantFilter(StringColumnFilter):
    query_param: str = "participant"
    column: str = "participant__identifier"
    label: str = "Participant"


class ExperimentFilter(ChoiceColumnFilter):
    query_param: str = "experiment"
    column: str = "experiment_id"
    label: str = "Chatbot"

    def prepare(self, team, **kwargs):
        from apps.experiments.models import Experiment

        experiments = (
            Experiment.objects.working_versions_queryset().filter(team=team).values("id", "name").order_by("name")
        )
        self.options = [{"id": exp["id"], "label": exp["name"]} for exp in experiments]

    def parse_query_value(self, value) -> list[int]:
        values = []
        for v in self.values_list(value):
            try:
                values.append(int(v))
            except (ValueError, TypeError):
                continue
        return values


class StatusFilter(ChoiceColumnFilter):
    column: str = "status"
    label: str = "Status"
    options: list[str] = SessionStatus.for_chatbots()


class RemoteIdFilter(ChoiceColumnFilter):
    query_param: str = "remote_id"
    column: str = "participant__remote_id"
    label: str = "Remote ID"


class TimestampFilter(ColumnFilter):
    type: str = TYPE_TIMESTAMP
    options: list[dict[str, str]] = DATE_RANGE_OPTIONS

    def _get_date_as_utc(self, value) -> datetime | None:
        try:
            date_value = datetime.fromisoformat(value)
            # Convert date to UTC to compare it correctly with stored timestamps
            return date_value.astimezone(pytz.UTC)
        except (ValueError, TypeError, pytz.UnknownTimeZoneError):
            return None

    def apply_on(self, queryset, value, timezone=None) -> QuerySet:
        """Filter for timestamps on a specific date"""
        if date_value := self._get_date_as_utc(value):
            return queryset.filter(**{f"{self.column}__date": date_value})
        return queryset

    def apply_before(self, queryset, value, timezone=None) -> QuerySet:
        """Filter for timestamps before a specific date"""
        if date_value := self._get_date_as_utc(value):
            return queryset.filter(**{f"{self.column}__date__lt": date_value})
        return queryset

    def apply_after(self, queryset, value, timezone=None) -> QuerySet:
        """Filter for timestamps after a specific date"""
        if date_value := self._get_date_as_utc(value):
            date_value = date_value.astimezone(pytz.UTC)
            return queryset.filter(**{f"{self.column}__date__gt": date_value})
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

            range_starting_client_time = now_client - delta
            range_starting_utc_time = range_starting_client_time.astimezone(pytz.UTC)
            return queryset.filter(**{f"{self.column}__gte": range_starting_utc_time})
        except (ValueError, TypeError, pytz.UnknownTimeZoneError):
            return queryset
