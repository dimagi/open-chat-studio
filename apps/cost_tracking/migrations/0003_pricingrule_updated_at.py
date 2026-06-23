"""Reconcile `PricingRule.updated_at` between the model and the DB.

PR 1's 0001_initial declared `updated_at` on `UsageRecord` (via BaseTeamModel)
but not on `PricingRule`. Some legacy DBs already have the column from an
earlier draft of 0001 — `IF NOT EXISTS` keeps this migration idempotent
across both fresh installs and those legacy states. State_operations
mirrors the change into Django's migration graph so `makemigrations` no
longer flags drift.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cost_tracking", "0002_seed_pricing")]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE cost_tracking_pricingrule "
                "ADD COLUMN IF NOT EXISTS updated_at "
                "timestamp with time zone NOT NULL DEFAULT now()"
            ),
            reverse_sql="ALTER TABLE cost_tracking_pricingrule DROP COLUMN IF EXISTS updated_at",
            state_operations=[
                migrations.AddField(
                    model_name="pricingrule",
                    name="updated_at",
                    field=models.DateTimeField(auto_now=True),
                ),
            ],
        ),
    ]
