from urllib.parse import urlencode

from django.db import migrations
from django.http import QueryDict

from apps.dashboard.filter_format import convert_saved_filter_data


def migrate_saved_filters(apps, schema_editor):
    FilterSet = apps.get_model("filters", "FilterSet")
    batch_size = 500
    filter_sets_to_update = []

    for filter_set in FilterSet.objects.all().iterator(chunk_size=batch_size):
        raw_query = filter_set.filter_query_string or ""
        if not raw_query:
            continue

        if raw_query.startswith(("f_", "op_")):
            continue

        query_params = QueryDict(raw_query)
        legacy_filter_data = {
            key: values[0] if len(values) == 1 else values for key, values in query_params.lists()
        }
        converted = convert_saved_filter_data(legacy_filter_data)
        if converted != legacy_filter_data:
            filter_set.filter_query_string = urlencode(converted)
            filter_sets_to_update.append(filter_set)
            if len(filter_sets_to_update) >= batch_size:
                FilterSet.objects.bulk_update(filter_sets_to_update, ["filter_query_string"])
                filter_sets_to_update.clear()

    if filter_sets_to_update:
        FilterSet.objects.bulk_update(filter_sets_to_update, ["filter_query_string"])


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0001_initial"), ("filters", "0001_initial_updated")]

    operations = [migrations.RunPython(migrate_saved_filters, migrations.RunPython.noop)]
