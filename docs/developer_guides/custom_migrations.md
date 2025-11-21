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

Run with:
```bash
python manage.py my_migration              # Execute
python manage.py my_migration --dry-run    # Preview
python manage.py my_migration --force      # Re-run
```

### Django Migration

Use the utility functions within Django migrations:

```python
from django.db import migrations
from apps.data_migrations.utils.migrations import (
    check_migration_in_django_migration,
    mark_migration_in_django_migration,
)

MIGRATION_NAME = "populate_new_field_2024_11_21"

def migrate_data(apps, schema_editor):
    if check_migration_in_django_migration(apps, MIGRATION_NAME):
        return

    MyModel = apps.get_model("myapp", "MyModel")
    MyModel.objects.filter(new_field=None).update(new_field="default")

    mark_migration_in_django_migration(apps, MIGRATION_NAME)

class Migration(migrations.Migration):
    dependencies = [("myapp", "0001_initial")]
    operations = [migrations.RunPython(migrate_data, migrations.RunPython.noop)]
```

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
