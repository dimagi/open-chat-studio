# Generated manually

from django.contrib.postgres.operations import ValidateConstraint
from django.db import migrations, models
import django.contrib.postgres.fields


class Migration(migrations.Migration):

    dependencies = [
        ('experiments', '0117_fix_connect_duplicate_participants'),
    ]

    operations = [
        migrations.AddField(
            model_name='experiment',
            name='participant_access_level',
            field=models.CharField(
                choices=[
                    ('open', 'Open Access (Public)'),
                    ('allow_list', 'Allow List'),
                    ('deny_list', 'Deny List')
                ],
                default='open',
                help_text='Controls who can access this chatbot',
                max_length=20
            ),
        ),
        migrations.AddField(
            model_name='experiment',
            name='participant_denylist',
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=128),
                blank=True,
                default=list,
                size=None
            ),
        ),
        # Data migration to set access_level based on existing allowlist
        migrations.RunPython(
            code=lambda apps, schema_editor: _set_access_level_from_allowlist(apps),
            reverse_code=migrations.RunPython.noop,
        ),
    ]


def _set_access_level_from_allowlist(apps):
    """
    Migrate existing experiments to use the new access_level field:
    - If allowlist is empty: access_level = 'open'
    - If allowlist has entries: access_level = 'allow_list'
    """
    Experiment = apps.get_model('experiments', 'Experiment')
    
    for experiment in Experiment.objects.all():
        if experiment.participant_allowlist:
            experiment.participant_access_level = 'allow_list'
        else:
            experiment.participant_access_level = 'open'
        experiment.save(update_fields=['participant_access_level'])
