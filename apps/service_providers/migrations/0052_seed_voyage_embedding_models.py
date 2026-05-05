from django.db import migrations

from apps.service_providers.migration_utils import embedding_model_migration


class Migration(migrations.Migration):

    dependencies = [
        ("service_providers", "0051_add_voyage_ai_provider"),
    ]

    operations = [
        embedding_model_migration(),
    ]
