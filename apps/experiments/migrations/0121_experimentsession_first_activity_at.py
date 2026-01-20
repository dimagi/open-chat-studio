from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def populate_first_activity_at(apps, schema_editor):
    """
    Populate the first_activity_at field for ExperimentSession based on the
    created_at timestamp of the first ChatMessage in each session's chat.
    """
    ExperimentSession = apps.get_model("experiments", "ExperimentSession")
    ChatMessage = apps.get_model("chat", "ChatMessage")

    # Count sessions to update
    total_count = ExperimentSession.objects.filter(first_activity_at__isnull=True).count()

    if total_count == 0:
        print("No sessions need first_activity_at update")
        return

    print(f"Updating first_activity_at for {total_count} sessions...")

    # Subquery to get the created_at of the first message for each chat
    # Using [:1] to ensure only one value is returned
    first_message_subquery = (
        ChatMessage.objects.filter(chat_id=OuterRef("chat_id"))
        .order_by("created_at")
        .values("created_at")[:1]
    )

    # Single UPDATE query using subquery
    updated_count = ExperimentSession.objects.filter(first_activity_at__isnull=True).update(
        first_activity_at=Subquery(first_message_subquery),
    )

    print(f"Updated {updated_count} sessions with first_activity_at")


class Migration(migrations.Migration):
    dependencies = [
        ('experiments', '0120_backfill_session_fields'),
        ('chat', '0021_alter_chatmessage_created_at'),  # Ensure chat messages exist
    ]

    operations = [
        migrations.AddField(
            model_name='experimentsession',
            name='first_activity_at',
            field=models.DateTimeField(blank=True, help_text='Timestamp of the first user interaction', null=True),
        ),
        migrations.RunPython(populate_first_activity_at, reverse_code=migrations.RunPython.noop, elidable=True),
    ]
