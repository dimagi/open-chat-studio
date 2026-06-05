from datetime import timedelta

from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


def backfill_session_token_required(apps, schema_editor):
    """Sessions active in the last 24h keep legacy (token-less) access so live
    conversations are not interrupted; everything older is locked down."""
    ExperimentSession = apps.get_model("experiments", "ExperimentSession")
    cutoff = timezone.now() - timedelta(hours=24)
    recent_ids = ExperimentSession.objects.filter(
        Q(chat__messages__created_at__gte=cutoff) | Q(created_at__gte=cutoff)
    ).values("id")
    ExperimentSession.objects.filter(id__in=recent_ids).update(session_token_required=False)


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0140_drop_experiment_columns"),
    ]

    operations = [
        migrations.AddField(
            model_name="experimentsession",
            name="session_token_required",
            field=models.BooleanField(
                default=True,
                help_text="Require a signed session token (or authenticated user) for chat API access to this session.",
            ),
        ),
        migrations.RunPython(backfill_session_token_required, migrations.RunPython.noop),
    ]
