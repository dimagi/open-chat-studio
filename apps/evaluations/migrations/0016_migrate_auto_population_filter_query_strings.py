from django.db import migrations
from django.http import QueryDict

from apps.web.dynamic_filters.datastructures import FilterParams


def migrate_auto_population_filter_query_strings(apps, schema_editor):
    DatasetAutoPopulationRule = apps.get_model("evaluations", "DatasetAutoPopulationRule")
    batch_size = 500
    rules_to_update = []

    for rule in DatasetAutoPopulationRule.objects.all().iterator(chunk_size=batch_size):
        raw_query = rule.filter_query_string or ""
        if not raw_query:
            continue

        if raw_query.startswith("f_") or raw_query.startswith("op_"):
            continue

        try:
            params = FilterParams(QueryDict(raw_query))
        except Exception:
            continue

        if not params.filters:
            continue

        rule.filter_query_string = str(params.to_query())
        rules_to_update.append(rule)
        if len(rules_to_update) >= batch_size:
            DatasetAutoPopulationRule.objects.bulk_update(rules_to_update, ["filter_query_string"])
            rules_to_update.clear()

    if rules_to_update:
        DatasetAutoPopulationRule.objects.bulk_update(rules_to_update, ["filter_query_string"])


class Migration(migrations.Migration):
    dependencies = [("evaluations", "0015_auto_populate_schema")]

    operations = [migrations.RunPython(migrate_auto_population_filter_query_strings, migrations.RunPython.noop)]
