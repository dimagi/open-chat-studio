from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data
from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0065_alter_voiceprovider_type"),
        ("cost_tracking", "0001_initial"),
        # The data migration below queries Team with live models, so all Team
        # schema changes must be applied first.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
    ]

    operations = [
        # Add gemini-3.6-flash and gemini-3.5-flash-lite for the `google` and
        # `google_vertex_ai` providers (1M context).
        llm_model_migration(),
        # Seed pricing for the newly added Gemini models.
        load_pricing_data(),
    ]
