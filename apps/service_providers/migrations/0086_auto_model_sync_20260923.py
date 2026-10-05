from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0085_deprecate_google_gemini_2_5"),
        # Retained so the graph stays stable for environments that already applied this.
        ("cost_tracking", "0008_rate_update_20260904"),
        # remove_deprecated_models queries Team with live models, so all Team
        # schema changes must be applied first.
        ("teams", "0013_team_files_export_team_files_export_task_id"),
        # remove_deprecated_models repoints evaluators off the models it deletes, so the
        # Evaluator FK must be in this migration's app state.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
    ]

    operations = [
        # llm_model_migration() and load_pricing_data() moved to
        # 0088_auto_model_sync_20260930 so they run only once per deploy.
        # remove_deprecated_models and notify_deprecated_models moved to
        # 0089_deprecate_claude_sonnet_4_5 so they run only once per deploy.
    ]
