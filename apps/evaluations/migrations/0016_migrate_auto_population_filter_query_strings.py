import json
import re
from urllib.parse import urlencode

from django.db import migrations
from django.http import QueryDict
from apps.dashboard.filter_format import convert_saved_filter_data


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

        converted_query_string = convert_saved_filter_data(raw_query)
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
