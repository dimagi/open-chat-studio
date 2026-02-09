# Generated migration for notification mutes

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ocs_notifications', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('teams', '0008_drop_unused_waffle_tables'),
    ]

    operations = [
        migrations.CreateModel(
            name='NotificationMute',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('notification_type', models.CharField(blank=True, default='', help_text='Notification slug/type to mute. Leave empty to mute all.', max_length=255)),
                ('muted_until', models.DateTimeField(blank=True, help_text='When the mute expires. NULL means forever.', null=True)),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='teams.team', verbose_name='Team')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notification_mutes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name_plural': 'Notification Mutes',
                'unique_together': {('user', 'team', 'notification_type')},
            },
        ),
        migrations.AddIndex(
            model_name='notificationmute',
            index=models.Index(fields=['user', 'team', 'muted_until'], name='ocs_notific_user_id_b26f27_idx'),
        ),
    ]
