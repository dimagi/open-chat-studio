from django.core.management import call_command
from django.db import migrations, models


def migrate_experiments(apps, schema_editor):
    call_command("migrate_nonpipeline_to_pipeline_experiments", skip_confirmation=True)


class Migration(migrations.Migration):

    dependencies = [
        ('experiments', '0115_alter_participant_remote_id'),
    ]

    operations = [
        migrations.RunPython(migrate_experiments, elidable=True)
    ]
