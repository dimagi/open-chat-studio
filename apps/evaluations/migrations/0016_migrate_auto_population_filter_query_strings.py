import json
import re
from urllib.parse import urlencode

from django.db import migrations
from django.http import QueryDict


def _convert_legacy_filter_query_string_to_new_format(raw_query: str) -> str:
    query_params = QueryDict(raw_query)
    legacy_filter_data = {
        key: values[0] if len(values) == 1 else values for key, values in query_params.lists()
    }

    legacy_filters = []
    for key, value in legacy_filter_data.items():
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

    return urlencode(converted)


def migrate_auto_population_filter_query_strings(apps, schema_editor):
    DatasetAutoPopulationRule = apps.get_model("evaluations", "DatasetAutoPopulationRule")
    batch_size = 500
    rules_to_update = []

    for rule in DatasetAutoPopulationRule.objects.all().iterator(chunk_size=batch_size):
        raw_query = rule.filter_query_string or ""
        if not raw_query:
            continue

        if raw_query.startswith(("f_", "op_")):
            continue

        converted_query_string = _convert_legacy_filter_query_string_to_new_format(raw_query)
        if converted_query_string and converted_query_string != raw_query:
            rule.filter_query_string = converted_query_string
            rules_to_update.append(rule)
            if len(rules_to_update) >= batch_size:
                DatasetAutoPopulationRule.objects.bulk_update(rules_to_update, ["filter_query_string"])
                rules_to_update.clear()

    if rules_to_update:
        DatasetAutoPopulationRule.objects.bulk_update(rules_to_update, ["filter_query_string"])


class Migration(migrations.Migration):
    dependencies = [("evaluations", "0015_auto_populate_schema")]

    operations = [migrations.RunPython(migrate_auto_population_filter_query_strings, migrations.RunPython.noop)]
