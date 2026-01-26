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

## Two / Three-Phase Deployment Workflow

When adding new fields that require data backfilling, use a two or three-phase deployment to safely migrate data in production:

### Phase 1: Add Field and Initial Migration

**Goal**: Add the new field and backfill existing data.

1. **Create the data model changes**:
   ```python
   # models.py
   class User(models.Model):
       name = models.CharField(max_length=100)
       normalized_name = models.CharField(max_length=100, blank=True)  # New field
   ```

2. **Create a Django schema migration**:
   ```bash
   python manage.py makemigrations
   ```

3. **Create the data migration command**:
   ```python
   # management/commands/backfill_normalized_names.py
   from apps.data_migrations.management.commands.base import IdempotentCommand
   from apps.users.models import User

   class Command(IdempotentCommand):
       help = "Backfill normalized names for existing users"
       migration_name = "backfill_normalized_names_2024_12_15"

       def perform_migration(self, dry_run=False):
           users = User.objects.filter(normalized_name="")

           if dry_run:
               self.stdout.write(f"Would update {users.count()} users")
               return

           updated = 0
           for user in users:
               user.normalized_name = user.name.lower()
               user.save()
               updated += 1

           return updated
   ```

4. **Deploy and run**:
   - Deploy the PR with model and data migration
   - Run manually in production: `python manage.py backfill_normalized_names`
   - Verify the data was migrated correctly

### Phase 2: Add Django Migration Top-Up

**Goal**: Automatically migrate any new records created after Phase 1.

1. **Keep the field as optional** (no model changes needed):
   ```python
   # models.py - unchanged from Phase 1
   class User(models.Model):
       name = models.CharField(max_length=100)
       normalized_name = models.CharField(max_length=100, blank=True)  # Still optional
   ```

2. **Create a Django migration with the data migration**:
   ```python
   # migrations/0XXX_backfill_normalized_names_topup.py
   from django.db import migrations
   from apps.data_migrations.utils.migrations import RunDataMigration

   class Migration(migrations.Migration):
       dependencies = [("users", "0XXX_previous_migration")]

       operations = [
           RunDataMigration("backfill_normalized_names", command_options={"force": True}),
       ]
   ```

3. **Deploy**:
   - The migration runs automatically during deployment
   - The data migration command processes any records created between Phase 1 and Phase 2
   - No constraint changes, so no risk of deploy failures

### Phase 3: Make Field Required (Optional)

**Goal**: Optionally enforce the field constraint after all data is migrated.

**Note**: This phase is only needed if you want to make the field required. If the field can remain optional, you can stop after Phase 2.

1. **Update the model to make the field required**:
   ```python
   # models.py
   class User(models.Model):
       name = models.CharField(max_length=100)
       normalized_name = models.CharField(max_length=100)  # Remove blank=True
   ```

2. **Create a schema migration**:
   ```bash
   python manage.py makemigrations
   ```

   This generates:
   ```python
   # migrations/0XXX_alter_user_normalized_name.py
   from django.db import migrations

   class Migration(migrations.Migration):
       dependencies = [("users", "0XXX_previous_migration")]

       operations = [
           migrations.AlterField(
               model_name="user",
               name="normalized_name",
               field=models.CharField(max_length=100),  # No longer blank=True
           ),
       ]
   ```

3. **Deploy**:
   - The constraint is applied to the field
   - All data should already be migrated from Phase 2

### Why Three Phases?

**Non-Blocking Deploys**: Long-running data migrations can block deployments. Running manually in Phase 1 keeps deploys fast and allows you to monitor progress separately.

**Deploy Safety**: Phase 2 keeps the field optional during the automatic top-up migration. This prevents deployment failures from constraint violations if any unmigrated records exist.

**Constraint Isolation**: Phase 3 (optional) separates the constraint change from the data migration. If you need to make the field required, you can do so safely after confirming all data is migrated. If the field can remain optional, Phase 3 isn't necessary.

**Performance**: Run the initial backfill manually with monitoring. Large datasets can be processed in batches or during low-traffic periods.

**Flexibility**: Test the migration in production with the field as optional. If issues arise in Phase 2, you can fix data before optionally enforcing the constraint in Phase 3.

**Zero Downtime**: Application code continues working with the optional field through Phases 1 and 2. Phase 3 (if needed) only proceeds after verifying all data is migrated.

### Alternative: Single-Phase for Simple Cases

For small datasets or non-critical fields, you can combine all three phases:

```python
# migrations/0XXX_add_normalized_name.py
from django.db import migrations
from apps.data_migrations.utils.migrations import RunDataMigration

class Migration(migrations.Migration):
    dependencies = [("users", "0XXX_previous_migration")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="normalized_name",
            field=models.CharField(max_length=100, blank=True),
        ),
        RunDataMigration("backfill_normalized_names"),
        migrations.AlterField(
            model_name="user",
            name="normalized_name",
            field=models.CharField(max_length=100),  # Now required
        ),
    ]
```

**Use single-phase only when**:
- Dataset is small (< 10,000 records)
- Migration is fast (< 30 seconds)
- Field is non-critical
- You have tested thoroughly in staging
