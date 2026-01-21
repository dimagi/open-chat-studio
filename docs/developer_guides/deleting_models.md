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

## Maintenance: Clearing Old Models from DELETED_MODELS

The `DELETED_MODELS` list should be cleaned up periodically to avoid accumulating stale entries.

### When to Remove Models

It is safe to remove models from `DELETED_MODELS` when **both** conditions are met:

1. The `remove_deprecated_models` command has run successfully in **all environments** (development, staging, production)
2. The models have been in the `main` branch for **more than 1 month**

### Why Wait 1 Month?

- Ensures all environments have executed the migration
- Gives time for any rollback scenarios
- Allows thorough testing across deployment cycles
- Accounts for environments that deploy less frequently

### How to Clean Up

1. **Verify migration has run everywhere**:
   ```bash
   # Check if migration is applied
   python manage.py custom_migrations list --name remove_deprecated_models
   ```

2. **Check git history** to confirm models have been in main for 1+ month:
   ```bash
   # Find when models were added to DELETED_MODELS
   git log -p --all -S 'DELETED_MODELS' -- apps/service_providers/llm_service/default_models.py
   ```

3. **Remove old entries** from `DELETED_MODELS`:
   ```python
   DELETED_MODELS = [
       # Keep recent additions (< 1 month in main)
       ("openai", "gpt-4"),
       ("anthropic", "claude-2.0"),

       # Remove these - added 2 months ago, migrated everywhere
       # ("azure", "gpt-35-turbo"),
       # ("groq", "llama3-70b-8192"),
   ]
   ```

4. **Commit the cleanup**:
   ```bash
   git add apps/service_providers/llm_service/default_models.py
   git commit -m "chore: clean up DELETED_MODELS list"
   ```

### Important Notes

- **Do not** remove models that are still in active migrations
- **Do not** remove models if any environment hasn't deployed the migration yet
- This cleanup is purely for code hygiene; it doesn't affect functionality once migrations have run
