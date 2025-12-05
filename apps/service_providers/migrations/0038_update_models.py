from django.db import migrations

from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):

    dependencies = [
        ('service_providers', '0037_alter_embeddingprovidermodel_type_and_more'),
    ]

    operations = [
        llm_model_migration(),
    ]
