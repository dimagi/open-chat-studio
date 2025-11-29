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
