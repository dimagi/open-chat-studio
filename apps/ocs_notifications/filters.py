from collections.abc import Sequence
from typing import Any, ClassVar

from apps.ocs_notifications.models import LevelChoices
from apps.web.dynamic_filters.base import ChoiceColumnFilter, MultiColumnFilter
from apps.web.dynamic_filters.column_filters import TimestampFilter
from apps.web.dynamic_filters.datastructures import FilterParams, serialize_csv_tilde_values


class ReadFilter(ChoiceColumnFilter):
    """Filter notifications by read status."""

    query_param: str = "read"
    column: str = "read"
    label: str = "Read Status"
    options: list[dict[str, Any]] = [
        {"id": "true", "label": "Read"},
        {"id": "false", "label": "Unread"},
    ]

    def parse_query_value(self, query_value) -> list[bool] | None:
        """Convert string values 'true'/'false' to boolean."""
        values = self.values_list(query_value)
        if not values:
            return None
        return [val.lower() == "true" for val in values]


class MuteFilter(ChoiceColumnFilter):
    query_param: str = "muted"
    # The column is annotated on the queryset in the view, so we can filter on it directly
    column: str = "is_muted"
    label: str = "Muted"
    options: list[dict[str, Any]] = [
        {"id": "true", "label": "Muted"},
        {"id": "false", "label": "Not Muted"},
    ]

    def parse_query_value(self, query_value) -> list[bool] | None:
        """Convert string values 'true'/'false' to boolean."""
        values = self.values_list(query_value)
        if not values:
            return None
        return [val.lower() == "true" for val in values]


class SeverityLevelFilter(ChoiceColumnFilter):
    """Filter notifications by level/level."""

    query_param: str = "level"
    column: str = "event_type__level"
    label: str = "Severity Level"
    options: list[str | dict[str, Any]] = [{"id": choice[0], "label": choice[1]} for choice in LevelChoices.choices]


class TeamFilter(ChoiceColumnFilter):
    """Filter notifications by team. Defaults to showing notifications from every team the user belongs to."""

    query_param: str = "team"
    column: str = "team_id"
    label: str = "Team"

    def prepare(self, team, **kwargs):
        user = kwargs.get("user")
        teams = user.teams.all().order_by("name") if user else []
        self.options = [{"id": t.id, "label": t.name} for t in teams]

    def parse_query_value(self, value) -> list[int] | None:  # ty: ignore[invalid-method-override]
        values = []
        for v in self.values_list(value):
            try:
                values.append(int(v))
            except (ValueError, TypeError):
                continue
        if not values:
            return None
        return values


class UserNotificationFilter(MultiColumnFilter):
    """Filter for user notifications using multiple column filters."""

    slug: ClassVar[str] = "notification"
    date_range_column: ClassVar[str] = "notification_date"
    filters: ClassVar[Sequence] = [
        TeamFilter(),
        ReadFilter(),
        TimestampFilter(label="Notification Date", column="latest_event_created_at", query_param="notification_date"),
        SeverityLevelFilter(),
        MuteFilter(),
    ]


# Marks a toggle-button-generated URL as stating the complete, authoritative filter state --
# see resolve_notification_filter_params, which must NOT fall back to the (one-click-stale)
# HX-Current-URL header for these requests the way it correctly does for sort/pagination links
# that intentionally omit filter params.
EXPLICIT_FILTERS_PARAM = "explicit_filters"


def resolve_notification_filter_params(request) -> FilterParams:
    """Resolve the filters that should apply to this request.

    Sort/pagination links inside the table intentionally omit filter params and rely on
    `FilterParams.from_request`'s `HX-Current-URL` fallback to recover the currently active
    ones. The toggle buttons (build_toggle_options) instead always state the complete,
    authoritative filter set directly in their own query string and mark it with
    `EXPLICIT_FILTERS_PARAM` -- consulting that header for them would be wrong, since it
    reflects the browser's address bar as of *before* this response's own `hx-push-url` takes
    effect, one click stale.
    """
    if EXPLICIT_FILTERS_PARAM in request.GET:
        return FilterParams(request.GET)
    return FilterParams.from_request(request)


def build_toggle_options(filter_instance: ChoiceColumnFilter, request) -> list[dict]:
    """Build one toggle-button option per choice on `filter_instance`, each carrying the query
    string that results from toggling that choice against the request's current filters.

    A page-local alternative to the big dynamic-filter panel for a `ChoiceColumnFilter` with a
    small, fixed set of options -- drives the same `f_<column>`/`op_<column>` query params (see
    `FilterParams`) without touching the shared filter UI other pages depend on. Multiple active
    choices combine with "any of" (OR), same as the underlying filter already supports; toggling
    off the last one drops the column's params entirely so "all" is the default state.
    """
    filter_params = resolve_notification_filter_params(request)
    active_values: set[str] = set()
    for column_filter in filter_params.get_all(filter_instance.query_param):
        active_values.update(str(v) for v in filter_instance.parse_query_value(column_filter.value) or [])

    f_key, op_key = f"f_{filter_instance.query_param}", f"op_{filter_instance.query_param}"
    base_query = request.GET.copy()
    base_query.pop(f_key, None)
    base_query.pop(op_key, None)

    options = []
    for choice in filter_instance.options:
        option_id = str(choice["id"] if isinstance(choice, dict) else choice)
        label = choice["label"] if isinstance(choice, dict) else choice
        is_active = option_id in active_values
        new_values = active_values - {option_id} if is_active else active_values | {option_id}

        query = base_query.copy()
        if new_values:
            query[f_key] = serialize_csv_tilde_values(sorted(new_values))
            query[op_key] = "any of"
        query[EXPLICIT_FILTERS_PARAM] = "1"

        options.append({"label": label, "is_active": is_active, "query_string": query.urlencode()})
    return options
