# Deleting LLM Models

When removing deprecated LLM models from the platform, follow this process to ensure proper cleanup and user notification:

## Step 1: Update Model Definitions

In `apps/service_providers/llm_service/default_models.py`:

1. **Remove** the model from `DEFAULT_LLM_PROVIDER_MODELS`:
   ```python
   DEFAULT_LLM_PROVIDER_MODELS = {
       "openai": [
           # Model("gpt-4", k(8)),  # Remove this line
           Model("gpt-4o", 128000),
           # ... other models
       ],
   }
   ```

2. **Add** the model to `DELETED_MODELS`:
   ```python
   DELETED_MODELS = [
       ("openai", "gpt-4"),
       ("anthropic", "claude-2.0"),
       # ... other deleted models
   ]
   ```

## Step 2: Create Django Migration

Create a migration in `apps/service_providers/migrations/` that performs the model update:

```python
from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0040_previous_migration"),
    ]

    operations = [
        # Update model list (marks deprecated models)
        llm_model_migration(),

        # Clean up references and notify teams
        RunDataMigration("remove_deprecated_models"),
    ]
```

## Testing

Before deploying:

```bash
# Preview what will be deleted
python manage.py remove_deprecated_models --dry-run

# Verbose output with team details
python manage.py remove_deprecated_models --dry-run -v 2

# Run the migration
python manage.py migrate
```
