from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0085_deprecate_google_gemini_2_5"),
        # Retained so the graph stays stable for environments that already applied this.
        ("cost_tracking", "0008_rate_update_20260904"),
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # llm_model_migration(), remove_deprecated_models, notify_deprecated_models and
        # load_pricing_data() moved to 0087_auto_model_sync_20260924 so they run only once
        # per deploy.
    ]
