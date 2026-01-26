from django.db import migrations, transaction, models

from django.db.models import OuterRef, Subquery



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
    ]
