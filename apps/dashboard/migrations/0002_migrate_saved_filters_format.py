from django.db import migrations

from apps.dashboard.filter_format import convert_saved_filter_data


def migrate_saved_filters(apps, schema_editor):
    DashboardFilter = apps.get_model("dashboard", "DashboardFilter")
    for filter_obj in DashboardFilter.objects.all():
        if not isinstance(filter_obj.filter_data, dict):
            continue

        converted = convert_saved_filter_data(filter_obj.filter_data)
        if converted != filter_obj.filter_data:
            filter_obj.filter_data = converted
            filter_obj.save(update_fields=["filter_data"])


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0001_initial")]

    operations = [migrations.RunPython(migrate_saved_filters, migrations.RunPython.noop)]
