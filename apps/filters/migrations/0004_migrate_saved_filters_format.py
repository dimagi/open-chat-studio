import csv
import json
import re
from io import StringIO
from urllib.parse import urlencode

from django.db import migrations
from django.http import QueryDict

# Self-contained copy of the legacy -> new filter conversion. Kept independent of the live
# apps.web.dynamic_filters.filter_format helper so that future edits to that code cannot
# silently change what this migration does for anyone who has not run it yet.

_LEGACY_FILTER_KEY = re.compile(r"filter_(\d+)_(column|operator|value)")
_LEGACY_COLUMN_KEY = re.compile(r"filter_\d+_column")


def _serialize_csv_tilde_values(values):
    """Serialize list values into the tilde-delimited CSV wire format.

    Items containing the ``~`` separator or a quote are quoted so they round-trip through
    the parser (e.g. ``["tag~2", "a"]`` -> ``"tag~2"~a``) instead of being silently split.
    """
    output = StringIO()
    writer = csv.writer(output, delimiter="~", quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator="")
    writer.writerow([str(item) for item in values])
    return output.getvalue().rstrip("\r\n")


def _convert_legacy_query_string(raw_query):
    """Convert a legacy ``filter_<n>_<field>`` query string to the new f_/op_ format.

    Returns the converted query string, or ``None`` if ``raw_query`` is not in the legacy
    format (detected positively by the presence of a ``filter_<n>_column`` key, so an
    already-converted string leading with an unrelated param is left alone).
    """
    query_params = QueryDict(raw_query)
    if not any(_LEGACY_COLUMN_KEY.fullmatch(key) for key in query_params):
        return None

    legacy_filters = []
    for key in query_params:
        match = _LEGACY_FILTER_KEY.fullmatch(key)
        if not match:
            continue
        index = int(match.group(1))
        field = match.group(2)
        while len(legacy_filters) <= index:
            legacy_filters.append({})
        legacy_filters[index][field] = query_params[key]

    converted = {}
    for legacy_filter in legacy_filters:
        column_name = legacy_filter.get("column")
        if not column_name:
            continue
        if "value" in legacy_filter:
            value = legacy_filter["value"]
            try:
                parsed = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                parsed = None
            if isinstance(parsed, list):
                value = _serialize_csv_tilde_values(parsed)
            converted[f"f_{column_name}"] = value
        if "operator" in legacy_filter:
            converted[f"op_{column_name}"] = legacy_filter["operator"]

    return urlencode(converted)


def migrate_saved_filters(apps, schema_editor):
    FilterSet = apps.get_model("filters", "FilterSet")
    batch_size = 500
    filter_sets_to_update = []

    for filter_set in FilterSet.objects.all().iterator(chunk_size=batch_size):
        raw_query = filter_set.filter_query_string or ""
        if not raw_query:
            continue

        converted = _convert_legacy_query_string(raw_query)
        if converted and converted != raw_query:
            filter_set.filter_query_string = converted
            filter_sets_to_update.append(filter_set)
            if len(filter_sets_to_update) >= batch_size:
                FilterSet.objects.bulk_update(filter_sets_to_update, ["filter_query_string"])
                filter_sets_to_update.clear()

    if filter_sets_to_update:
        FilterSet.objects.bulk_update(filter_sets_to_update, ["filter_query_string"])


class Migration(migrations.Migration):
    dependencies = [("filters", "0003_alter_filterset_table_type")]

    operations = [migrations.RunPython(migrate_saved_filters, migrations.RunPython.noop)]
