# Managing LLM Model Lifecycle

This guide covers two distinct processes for managing LLM models that are being retired:

- **Deprecating** a model: the model is kept in the system but flagged as deprecated. Users see a warning in the UI and are notified of the recommended replacement so they can update before the model is deleted.
- **Deleting** a model: the model is fully removed from the platform. Any remaining references are auto-migrated to a replacement if one is specified, and affected teams are notified.

| | Deprecation | Deletion |
|---|---|---|
| Removed from `DEFAULT_LLM_PROVIDER_MODELS` | No (marked `deprecated=True`) | Yes |
| Added to `DELETED_MODELS` | No | Yes |
| Model removed from DB | No | Yes |
| Auto-migration to replacement | No | Optional |
| Maintenance step to clean up list | No | Yes |
| User notification | Optional | Yes |

---

## Deprecating a Model

Deprecation marks a model as unsupported while keeping it accessible. Deprecated models show a warning in the UI. If a replacement is specified, users are notified so they can migrate before the model is eventually deleted.

### Step 1: Update Model Definitions

In `apps/service_providers/llm_service/default_models.py`, set `deprecated=True` on the model. Optionally set `replacement` to the name of the recommended successor model — this is included in the notification to affected teams:

```python
DEFAULT_LLM_PROVIDER_MODELS = {
    "openai": [
        Model("gpt-4", k(8), deprecated=True, replacement="gpt-4o"),
        # ... other models
    ],
}
```

### Step 2: Create Django Migration

```python
from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0040_previous_migration"),
    ]

    operations = [
        # Update model list (marks deprecated models in DB)
        llm_model_migration(),

        # Optional: notify affected teams about the deprecation and recommended replacement
        RunDataMigration("notify_deprecated_models", command_options={"force": True}),
    ]
```

Include `RunDataMigration("notify_deprecated_models")` when you want to proactively alert teams that have active references to the deprecated model. The notification includes the replacement model name (if set) so users know what to switch to before deletion.

---

## Deleting a Model

Deletion fully removes a model from the platform. References are either auto-migrated to a replacement or cleared, and affected teams are notified.

### Step 1: Update Model Definitions

In `apps/service_providers/llm_service/default_models.py`:

1. **Remove** the model from `DEFAULT_LLM_PROVIDER_MODELS`:
   ```python
   DEFAULT_LLM_PROVIDER_MODELS = {
       "openai": [
           # Model("gpt-4", k(8), deprecated=True),  # Remove this line
           Model("gpt-4o", 128000),
           # ... other models
       ],
   }
   ```

2. **Add** the model to `DELETED_MODELS`. Optionally include a replacement model name to auto-migrate references instead of clearing them:
   ```python
   DELETED_MODELS = [
       # Without replacement: references to this model are set to None
       ("openai", "gpt-4"),

       # With replacement: references are updated to point to the replacement model
       ("anthropic", "claude-2.0", "claude-3-5-sonnet-latest"),
   ]
   ```

### Step 2: Create Django Migration

```python
from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration
from apps.service_providers.migration_utils import llm_model_migration


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0040_previous_migration"),
    ]

    operations = [
        # Update model list
        llm_model_migration(),

        # Clean up references and notify teams
        RunDataMigration("remove_deprecated_models", command_options={"force": True}),
    ]
```

---

## Auto-migrating Usages to a Replacement Model

When deleting a model, you can specify a replacement to automatically update all references to point to a different model. This avoids leaving users with broken or null model references.

Add a third element to the `DELETED_MODELS` tuple:

```python
DELETED_MODELS = [
    ("openai", "gpt-4", "gpt-4o"),  # All references updated to gpt-4o
]
```

When a replacement is specified:
- Pipeline node references are updated to use the replacement model
- Direct FK references (assistants, analyses, etc.) are updated to the replacement
- The deletion notification tells teams which replacement was applied

When no replacement is specified:
- Pipeline node references are set to `None`
- Teams are still notified that references were cleared

---

## User Notifications

Both the deprecation notification and the deletion command use the OCS notifications system (see [notifications guide](notifications.md)) rather than admin emails. Notifications are sent to team members with the `service_providers.change_llmprovidermodel` permission.

Notification helpers are defined in `apps/ocs_notifications/notifications.py`:

```python
# Notifies a team that one of their resources uses a deprecated model, with recommended replacement
deprecated_model_notification(team, old_model, replacement_model, affected_resources)

# Notifies a team that a deleted model's references were auto-migrated or cleared
deleted_model_notification(team, model_name, replacement_model, affected_resources)
```

Notifications include:
- Which model was deprecated/deleted
- The replacement model (if any)
- Affected chatbots, pipelines, and assistants
- Links to the affected resources

### Slug conventions

| Event | Slug |
|---|---|
| Deprecated model (user action needed) | `llm-model-deprecated` |
| Deleted model references cleared/migrated | `llm-model-deleted` |

---

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
