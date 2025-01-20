# Generated by Django 5.1.2 on 2025-01-17 11:39

import django.db.models.deletion
from django.db import migrations, models

def _populate_experiment(apps, schema_editor):
    apps.get_model('experiments', 'ParticipantData').objects.all().update(experiment_id=models.F("object_id"))

class Migration(migrations.Migration):

    dependencies = [
        ('experiments', '0105_participantdata_encryption_key_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='participantdata',
            name='experiment',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='experiments.experiment'),
        ),
        # Populate experiment field from object_id and content_type
        migrations.RunPython(_populate_experiment, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='participantdata',
            name='experiment',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='experiments.experiment'),
        ),
        # Alter indexes
        migrations.RemoveIndex(
            model_name='participantdata',
            name='experiments_content_78f1ee_idx',
        ),
        migrations.AlterUniqueTogether(
            name='participantdata',
            unique_together={('participant', 'experiment')},
        ),
        migrations.AddIndex(
            model_name='participantdata',
            index=models.Index(fields=['experiment'], name='experiments_experim_70f029_idx'),
        ),
        # Make content type and object id nullable
        migrations.AlterField(
            model_name='participantdata',
            name='content_type',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype'),
        ),
        migrations.AlterField(
            model_name='participantdata',
            name='object_id',
            field=models.PositiveIntegerField(null=True),
        ),
    ]
