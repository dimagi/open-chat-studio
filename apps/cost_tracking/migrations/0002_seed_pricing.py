from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [
        ("cost_tracking", "0001_initial"),
    ]

    operations = [
        load_pricing_data(),
    ]
