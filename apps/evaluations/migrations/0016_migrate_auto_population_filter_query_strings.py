from django.db import migrations
from django.http import QueryDict

from apps.web.dynamic_filters.datastructures import FilterParams


def migrate_auto_population_filter_query_strings(apps, schema_editor):
    DatasetAutoPopulationRule = apps.get_model("evaluations", "DatasetAutoPopulationRule")

    for rule in DatasetAutoPopulationRule.objects.all():
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

        rule.filter_query_string = params.to_query()
        rule.save(update_fields=["filter_query_string"])


class Migration(migrations.Migration):
    dependencies = [("evaluations", "0015_auto_populate_schema")]

    operations = [migrations.RunPython(migrate_auto_population_filter_query_strings, migrations.RunPython.noop)]
