from django.db import migrations

from apps.cost_tracking.migration_utils import load_pricing_data


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0069_add_deepseek_v4_flash"),
        ("cost_tracking", "0001_initial"),
        # Retained from when this migration ran llm_model_migration() /
        # notify_deprecated_models, so the graph stays stable for environments that
        # already applied it.
        ("evaluations", "0018_evaluator_llm_provider_fks"),
        ("teams", "0013_team_files_export_team_files_export_task_id"),
    ]

    operations = [
        # Add deepseek-v4-pro and mark deepseek-chat / deepseek-reasoner deprecated
        # (replacement: deepseek-v4-flash).
        # llm_model_migration() and notify_deprecated_models moved to 0071_deprecate_vertex_gemini_2_5
        #
        # Seed pricing for deepseek-v4-flash and deepseek-v4-pro (off-peak rates).
        load_pricing_data(),
    ]
