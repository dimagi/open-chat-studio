import json
import re
from urllib.parse import urlencode

from django.http import QueryDict


def convert_saved_filter_data(filter_data):
    """Convert legacy dashboard filter payloads to the new f_/op_ query style.

    Accepts either a mapping of legacy filter fields or a raw query string. When
    given a query string, the converted result is returned as a query string too;
    when given a dict, a dict is returned.
    """
    if isinstance(filter_data, str):
        query_params = QueryDict(filter_data)
        legacy_filter_data = {key: values[0] if len(values) == 1 else values for key, values in query_params.lists()}
        converted = convert_saved_filter_data(legacy_filter_data)
        return urlencode(converted, doseq=True)

    if not isinstance(filter_data, dict):
        return filter_data

    legacy_filters = []
    for key, value in filter_data.items():
        match = re.fullmatch(r"filter_(\d+)_((?:column|operator|value))", key)
        if not match:
            continue

        index = int(match.group(1))
        field = match.group(2)

        while len(legacy_filters) <= index:
            legacy_filters.append({})

        legacy_filters[index][field] = value

    converted = {}
    for legacy_filter in legacy_filters:
        column_name = legacy_filter.get("column")
        if not column_name:
            continue

        if "value" in legacy_filter:
            value = legacy_filter["value"]
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    parsed = None

                if isinstance(parsed, list):
                    value = "~".join(str(item) for item in parsed)
                elif value is not None and not isinstance(value, str):
                    value = str(value)
            elif isinstance(value, list):
                value = "~".join(str(item) for item in value)
            elif value is not None:
                value = str(value)

            converted[f"f_{column_name}"] = value

        if "operator" in legacy_filter:
            converted[f"op_{column_name}"] = legacy_filter["operator"]

    return converted
