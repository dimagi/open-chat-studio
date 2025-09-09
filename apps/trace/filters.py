import json

from django.db.models import Count, Q
from django.urls import reverse

from apps.experiments.filters import DynamicFilter, Operators, get_experiment_filter_options, get_filter_context_data
from apps.trace.models import TraceStatus


def get_trace_filter_context_data(team):
    span_tags = list(
        team.span_set.filter(tags__is_system_tag=True).distinct("tags__name").values_list("tags__name", flat=True)
    )

    table_url = reverse("trace:table", args=[team.slug])
    context = get_filter_context_data(team, DynamicTraceFilter.columns, "timestamp", table_url, "data-table")
    context.update(
        {
            "df_span_names": list(team.span_set.values_list("name", flat=True).distinct()),
            "df_state_list": TraceStatus.values,
            "df_experiment_list": get_experiment_filter_options(team),
            "df_available_tags": span_tags,
        }
    )
    return context


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

    def apply(self):
        queryset = self.prepare_queryset()
        param_source = self.parsed_params
        filter_conditions = Q()
        filter_applied = False
        special_conditions = []

        for i in range(30):
            filter_column = param_source.get(f"filter_{i}_column")
            if filter_column not in self.columns:
                continue
            filter_operator = param_source.get(f"filter_{i}_operator")
            filter_value = param_source.get(f"filter_{i}_value")

            if not all([filter_column, filter_operator, filter_value]):
                break

            filter_column = filter_column[0] if isinstance(filter_column, list) else filter_column
            filter_operator = filter_operator[0] if isinstance(filter_operator, list) else filter_operator
            filter_value = filter_value[0] if isinstance(filter_value, list) else filter_value

            condition = self._build_filter_condition(filter_column, filter_operator, filter_value)
            if condition:
                if isinstance(condition, tuple):
                    # Special handling for ALL_OF conditions
                    special_conditions.append(condition)
                else:
                    filter_conditions &= condition
                    filter_applied = True

        if filter_applied:
            queryset = queryset.filter(filter_conditions).distinct()

        # Handle special ALL_OF conditions
        for condition_type, values in special_conditions:
            if condition_type == "tags_all_of":
                # Annotate with count of matching tags and filter
                queryset = queryset.annotate(
                    matching_tag_count=Count("spans__tags__name", filter=Q(spans__tags__name__in=values), distinct=True)
                ).filter(matching_tag_count=len(values))
            elif condition_type == "span_names_all_of":
                # Annotate with count of matching span names and filter
                queryset = queryset.annotate(
                    matching_span_count=Count("spans__name", filter=Q(spans__name__in=values), distinct=True)
                ).filter(matching_span_count=len(values))

        return queryset

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
            # Use a special approach for ALL_OF that requires count-based filtering
            # We'll mark this as needing special handling by returning a special Q object
            return ("tags_all_of", selected_tags)

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
            # Use a special approach for ALL_OF that requires count-based filtering
            return ("span_names_all_of", selected_names)

        elif operator == Operators.EXCLUDES:
            return ~Q(spans__name__in=selected_names)
