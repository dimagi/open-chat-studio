import json
from datetime import datetime, timedelta

import pytz
from django.db.models import QuerySet

from .base import ColumnFilter, Operators
from .datastructures import ColumnFilterData


class ParticipantFilter(ColumnFilter):
    query_param = "participant"

    def apply_filter(self, queryset, column_filter: ColumnFilterData, timezone=None):
        """Build filter condition for participant"""
        if not column_filter.value:
            return queryset

        if column_filter.operator == Operators.EQUALS:
            return queryset.filter(participant__identifier=column_filter.value)
        elif column_filter.operator == Operators.CONTAINS:
            return queryset.filter(participant__identifier__icontains=column_filter.value)
        elif column_filter.operator == Operators.DOES_NOT_CONTAIN:
            return queryset.exclude(participant__identifier__icontains=column_filter.value)
        elif column_filter.operator == Operators.STARTS_WITH:
            return queryset.filter(participant__identifier__istartswith=column_filter.value)
        elif column_filter.operator == Operators.ENDS_WITH:
            return queryset.filter(participant__identifier__iendswith=column_filter.value)
        elif column_filter.operator == Operators.ANY_OF:
            value = json.loads(column_filter.value)
            return queryset.filter(participant__identifier__in=value)
        return None


class ExperimentFilter(ColumnFilter):
    query_param = "experiment"

    def apply_filter(self, queryset, column_filter: ColumnFilterData, timezone=None) -> QuerySet:
        """Build filter condition for experiment"""
        try:
            selected_experiment_ids = json.loads(column_filter.value)
            if not selected_experiment_ids:
                return queryset

            # Convert to integers if they're strings
            experiment_ids = []
            for exp_id in selected_experiment_ids:
                try:
                    experiment_ids.append(int(exp_id))
                except (ValueError, TypeError):
                    continue

            if not experiment_ids:
                return queryset

            if column_filter.operator == Operators.ANY_OF:
                return queryset.filter(experiment_id__in=experiment_ids)
            elif column_filter.operator == Operators.EXCLUDES:
                return queryset.exclude(experiment_id__in=experiment_ids)
        except json.JSONDecodeError:
            pass
        return queryset


class StatusFilter(ColumnFilter):
    def __init__(self, query_param: str):
        self.query_param = query_param

    def apply_filter(self, queryset, column_filter: ColumnFilterData, timezone=None) -> QuerySet:
        """Build filter condition for state"""
        try:
            selected_values = json.loads(column_filter.value)
        except json.JSONDecodeError:
            return queryset

        if not selected_values:
            return queryset

        if column_filter.operator == Operators.ANY_OF:
            return queryset.filter(status__in=selected_values)
        elif column_filter.operator == Operators.EXCLUDES:
            return queryset.exclude(status__in=selected_values)

        return queryset


class RemoteIdFilter(ColumnFilter):
    query_param = "remote_id"

    def apply_filter(self, queryset, column_filter: ColumnFilterData, timezone=None) -> QuerySet:
        """Build filter condition for remote_id"""
        try:
            selected_values = json.loads(column_filter.value)
        except json.JSONDecodeError:
            return queryset

        if not selected_values:
            return queryset

        if column_filter.operator == Operators.ANY_OF:
            return queryset.filter(participant__remote_id__in=selected_values)
        elif column_filter.operator == Operators.EXCLUDES:
            return queryset.exclude(participant__remote_id__in=selected_values)

        return queryset


class TimestampFilter(ColumnFilter):
    def __init__(self, db_column: str, query_param: str):
        self.db_column = db_column
        self.query_param = query_param

    def apply_filter(self, queryset, column_filter: ColumnFilterData, timezone=None) -> QuerySet:
        """Build filter condition for timestamp, supporting date and relative ranges like '1h', '7d'.
        For 1d 24h are subtracted i.e sessions in the range of 24h are shown not based on the date"""

        try:
            client_tz = pytz.timezone(timezone) if timezone else pytz.UTC
            now_client = datetime.now(client_tz)
            # Handle 'range' operator with relative time (e.g., '1h', '7d')
            if column_filter.operator == Operators.RANGE:
                if not column_filter.value.endswith(("h", "d", "m")):
                    return queryset
                num = int(column_filter.value[:-1])
                unit = column_filter.value[-1]

                if unit == "h":
                    delta = timedelta(hours=num)
                elif unit == "d":
                    delta = timedelta(days=num)
                elif unit == "m":
                    delta = timedelta(minutes=num)

                range_starting_client_time = now_client - delta
                range_starting_utc_time = range_starting_client_time.astimezone(pytz.UTC)
                return queryset.filter(**{f"{self.db_column}__gte": range_starting_utc_time})

            else:
                # No need to convert the date as it is in client's timezone
                date_value = datetime.fromisoformat(column_filter.value)
                if column_filter.operator == Operators.ON:
                    queryset = queryset.filter(**{f"{self.db_column}__date": date_value})
                elif column_filter.operator == Operators.BEFORE:
                    queryset = queryset.filter(**{f"{self.db_column}__date__lt": date_value})
                elif column_filter.operator == Operators.AFTER:
                    queryset = queryset.filter(**{f"{self.db_column}__date__gt": date_value})
        except (ValueError, TypeError, pytz.UnknownTimeZoneError):
            pass
        return queryset
