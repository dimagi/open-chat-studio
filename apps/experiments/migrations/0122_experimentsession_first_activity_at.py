from django.db import migrations, transaction, models

from django.db.models import OuterRef, Subquery


def populate_first_activity_at(apps, schema_editor):
    """
    Populate the first_activity_at field for ExperimentSession based on the
    created_at timestamp of the first ChatMessage in each session's chat.
    """
    ExperimentSession = apps.get_model("experiments", "ExperimentSession")
    ChatMessage = apps.get_model("chat", "ChatMessage")

    # Select all the IDs
    all_session_ids = list(
        ExperimentSession.objects.filter(
            first_activity_at__isnull=True
        ).values_list('id', flat=True)
    )

    total = len(all_session_ids)
    batch_size = 500

    print(f"Updating first_activity_at for {total} sessions in batches of {batch_size}")

    # Process collected sessions in a batch
    for i in range(0, total, batch_size):
        batch_ids = all_session_ids[i:i + batch_size]

        with transaction.atomic():
            first_human_message = ChatMessage.objects.filter(
                chat_id=OuterRef('chat_id'),
                message_type='human'  # Use string literal instead
            ).order_by('created_at').values('created_at')[:1]

            updated = ExperimentSession.objects.filter(
                id__in=batch_ids

            ).update(
                first_activity_at=Subquery(first_human_message)
            )

        processed = i + len(batch_ids)
        print(f"Processed {processed}/{total} (updated {updated} in this batch)")


class Migration(migrations.Migration):
    atomic = False
    dependencies = [
        ('experiments', '0121_remove_experiment_assistant'),
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
