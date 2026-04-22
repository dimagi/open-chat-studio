from django.db import migrations

from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):

    dependencies = [
        ('service_providers', '0046_alter_voiceprovider_type'),
    ]

    operations = [
        llm_model_migration(),
    ]
