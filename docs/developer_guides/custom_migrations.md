# Custom Migrations

The custom migrations system tracks data migrations that run outside Django's standard migration framework, ensuring they execute exactly once across all environments.

## Usage

### Management Command

Create a command that inherits from `IdempotentCommand`:

```python
from apps.data_migrations.management.commands.base import IdempotentCommand

class Command(IdempotentCommand):
    help = "Migrate user data to new format"
    migration_name = "migrate_user_data_v1_2024_11_21"

    def perform_migration(self, dry_run=False):
        users = User.objects.filter(needs_migration=True)

        if dry_run:
            self.stdout.write(f"Would update {users.count()} users")
            return

        updated = users.update(migrated=True)
        return updated
```

#### Optional fields:

* `atomic`: Set to False to disable atomic migration.
* `disable_audit`: Set to True to disable model auditing for this migration.

Run with:
```bash
python manage.py my_migration              # Execute
python manage.py my_migration --dry-run    # Preview
python manage.py my_migration --force      # Re-run
```

### Django Migration

Use `RunDataMigration` to run your management command within a Django migration:

```python
from django.db import migrations
from apps.data_migrations.utils.migrations import RunDataMigration

class Migration(migrations.Migration):
    dependencies = [("myapp", "0001_initial")]
    operations = [
        RunDataMigration("my_migration"),
    ]
```

This automatically handles idempotency.

## Managing Migrations

Use the `custom_migrations` command to view and manage applied migrations:

```bash
python manage.py custom_migrations list                 # List all
python manage.py custom_migrations list --name user     # Filter by name
python manage.py custom_migrations mark <name>          # Mark as applied
python manage.py custom_migrations unmark <name>        # Remove record
```

## Naming Convention

Use descriptive names with dates: `{description}_{version}_{YYYY_MM_DD}`

Examples:
- `migrate_user_data_v1_2024_11_21`
- `backfill_team_settings_2024_12_01`

## Deleting LLM Models

When removing deprecated LLM models from the platform, follow this process to ensure proper cleanup and user notification:

### Step 1: Update Model Definitions

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

### Step 2: Create Django Migration

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

### What Happens

The migration performs these operations in order:

1. **`llm_model_migration()`**:
   - Updates the database to reflect changes in `DEFAULT_LLM_PROVIDER_MODELS`
   - Marks models as deprecated but doesn't delete them yet

2. **`RunDataMigration("remove_deprecated_models")`**:
   - Identifies all chatbots, pipelines, and assistants using deleted models
   - Emails team admins with lists of affected resources and pipeline nodes
   - Sets all references to `None` (experiments, assistants, pipeline nodes)
   - Deletes the deprecated models from the database

### Email Notifications

Team admins receive emails listing:
- Removed model names (e.g., "openai/gpt-4")
- Affected chatbots with their pipeline nodes
- Affected standalone pipelines with their nodes
- Affected assistants

Example email excerpt:
```
Chatbots (2):
- My Bot (affected nodes: LLMResponse-abc123, RouterNode-def456)
- Test Bot

Pipelines (1):
- Processing Pipeline (affected nodes: LLMResponseWithPrompt-xyz789)
```

### Testing

Before deploying:

```bash
# Preview what will be deleted
python manage.py remove_deprecated_models --dry-run

# Verbose output with team details
python manage.py remove_deprecated_models --dry-run -v 2

# Run the migration
python manage.py migrate
```

### Example: Removing GPT-4 and Claude 2.0

```python
# 1. In default_models.py
DEFAULT_LLM_PROVIDER_MODELS = {
    "openai": [
        # Model("gpt-4", k(8)),  # Removed
        Model("gpt-4o", 128000),
    ],
    "anthropic": [
        # Model("claude-2.0", k(100)),  # Removed
        Model("claude-opus-4-20250514", k(200)),
    ],
}

DELETED_MODELS = [
    ("openai", "gpt-4"),
    ("anthropic", "claude-2.0"),
]

# 2. Create migration
# apps/service_providers/migrations/0041_remove_old_models.py
from django.db import migrations
from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration

class Migration(migrations.Migration):
    dependencies = [("service_providers", "0040_previous")]
    operations = [
        llm_model_migration(),
        RunDataMigration("remove_deprecated_models"),
    ]
```
