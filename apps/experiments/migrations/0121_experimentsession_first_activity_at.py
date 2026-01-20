from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('experiments', '0120_backfill_session_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='experimentsession',
            name='first_activity_at',
            field=models.DateTimeField(blank=True, help_text='Timestamp of the first user interaction', null=True),
        ),
    ]
