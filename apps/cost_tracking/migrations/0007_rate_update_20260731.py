from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [("cost_tracking", "0006_usagerecord_evaluation_config_usagerecord_source_and_more")]
    operations = [load_pricing_data()]
