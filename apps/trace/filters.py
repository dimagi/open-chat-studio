import json

from django.db.models import Q

from apps.experiments.filters import DynamicFilter, Operators


class DynamicTraceFilter(DynamicFilter):
    columns = [
        "participant",
        "tags",
        "remote_id",
        "timestamp",
        "span_name",
        "experiment",
        "status",
    ]

    def build_filter_condition(self, column, operator, value):
        if column == "participant":
            return self.build_participant_filter(operator, value)
        elif column == "tags":
            return self.build_tags_filter(operator, value)
        elif column == "remote_id":
            return self.build_remote_id_filter(operator, value)
        elif column == "timestamp":
            return self.build_timestamp_filter(operator, value, "timestamp", self.timezone)
        elif column == "span_name":
            return self.build_span_name_filter(operator, value)
        elif column == "experiment":
            return self.build_experiment_filter(operator, value)
        elif column == "status":
            return self.build_state_filter(operator, value)
        return None

    def build_tags_filter(self, operator, value):
        try:
            selected_tags = json.loads(value)
        except json.JSONDecodeError:
            return None

        if not selected_tags:
            return None

        if operator == Operators.ANY_OF:
            return Q(spans__tags__name__in=selected_tags)

        elif operator == Operators.ALL_OF:
            conditions = Q()

            for tag in selected_tags:
                conditions &= Q(spans__tags__name=tag)
            return conditions

        elif operator == Operators.EXCLUDES:
            return ~Q(spans__tags__name__in=selected_tags)

    def build_span_name_filter(self, operator, value):
        try:
            selected_names = json.loads(value)
        except json.JSONDecodeError:
            return None

        if not selected_names:
            return None

        if operator == Operators.ANY_OF:
            return Q(spans__name__in=selected_names)

        elif operator == Operators.ALL_OF:
            conditions = Q()

            for tag in selected_names:
                conditions &= Q(spans__name=tag)
            return conditions

        elif operator == Operators.EXCLUDES:
            return ~Q(spans__name__in=selected_names)
