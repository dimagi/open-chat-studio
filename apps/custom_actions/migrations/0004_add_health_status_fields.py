# Generated manually for health status feature

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('custom_actions', '0003_remove_customactionoperation_experiment_or_assistant_required_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='customaction',
            name='health_endpoint',
            field=models.URLField(blank=True, help_text='Optional health check endpoint', null=True),
        ),
        migrations.AddField(
            model_name='customaction',
            name='health_status',
            field=models.CharField(
                choices=[('unknown', 'Unknown'), ('up', 'Up'), ('down', 'Down')],
                default='unknown',
                max_length=20
            ),
        ),
        migrations.AddField(
            model_name='customaction',
            name='last_health_check',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
