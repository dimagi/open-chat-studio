import json
import re
from urllib.parse import urlencode

from django.http import QueryDict

from .datastructures import serialize_csv_tilde_values

_LEGACY_FILTER_KEY = re.compile(r"filter_(\d+)_(column|operator|value)")


def is_legacy_filter_data(keys) -> bool:
    """Positively detect the legacy ``filter_<n>_column`` format.

    A query string is only "legacy" if it actually contains a legacy column key.
    This avoids mistaking an already-converted string (which may lead with an
    unrelated param such as ``page=2&f_tags=x``) for something that needs converting.
    """
    return any(re.fullmatch(r"filter_(\d+)_column", key) for key in keys)


def convert_saved_filter_data(filter_data):
    """Convert legacy dashboard filter payloads to the new f_/op_ query style.

    Accepts either a mapping of legacy filter fields or a raw query string. When
    given a query string, the converted result is returned as a query string too;
    when given a dict, a dict is returned. Input that is not in the legacy format is
    returned unchanged, so this is safe to call idempotently.
    """
    if isinstance(filter_data, str):
        query_params = QueryDict(filter_data)
        if not is_legacy_filter_data(query_params):
            return filter_data
        legacy_filter_data = {key: values[0] if len(values) == 1 else values for key, values in query_params.lists()}
        converted = convert_saved_filter_data(legacy_filter_data)
        return urlencode(converted, doseq=True)

    if not isinstance(filter_data, dict):
        return filter_data

    if not is_legacy_filter_data(filter_data):
        return filter_data

    legacy_filters = []
    for key, value in filter_data.items():
        match = _LEGACY_FILTER_KEY.fullmatch(key)
        if not match:
            continue

        index = int(match.group(1))
        field = match.group(2)

        while len(legacy_filters) <= index:
            legacy_filters.append({})

        legacy_filters[index][field] = value

    # The legacy format keyed filters by position, so one column could carry several filters
    # (a date range is `after X` AND `before Y`). The new format keys by column and expresses
    # that as a repeated key, so values accumulate per key rather than overwriting. A filter
    # contributes to both the f_ and op_ list or to neither, which keeps the two lists
    # positionally aligned for the zip in FilterParams.
    converted: dict[str, list] = {}
    for legacy_filter in legacy_filters:
        column_name = legacy_filter.get("column")
        operator = legacy_filter.get("operator")
        if not column_name or not operator or "value" not in legacy_filter:
            continue

        converted.setdefault(f"f_{column_name}", []).append(_convert_legacy_value(legacy_filter["value"]))
        converted.setdefault(f"op_{column_name}", []).append(operator)

    # Collapse single-element lists back to scalars, matching how the legacy input is read above.
    return {key: values[0] if len(values) == 1 else values for key, values in converted.items()}


def _convert_legacy_value(value):
    """Render one legacy filter value in the new wire format.

    List values (stored as JSON in the legacy format) become tilde-delimited CSV; everything
    else is passed through as a string.
    """
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            parsed = None

        if isinstance(parsed, list):
            return serialize_csv_tilde_values(parsed)
        return value

    if isinstance(value, list):
        return serialize_csv_tilde_values(value)

    if value is not None:
        return str(value)

    return value
