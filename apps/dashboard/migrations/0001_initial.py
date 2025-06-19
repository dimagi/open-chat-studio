# Generated manually for dashboard app

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('teams', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DashboardCache',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cache_key', models.CharField(max_length=255)),
                ('data', models.JSONField()),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='teams.team')),
            ],
            options={
                'indexes': [
                    models.Index(fields=['team', 'cache_key', 'expires_at'], name='dashboard_d_team_id_9c8f4f_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='DashboardMetricsSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('total_experiments', models.IntegerField(default=0)),
                ('total_participants', models.IntegerField(default=0)),
                ('total_sessions', models.IntegerField(default=0)),
                ('total_messages', models.IntegerField(default=0)),
                ('active_experiments', models.IntegerField(default=0)),
                ('active_participants', models.IntegerField(default=0)),
                ('new_participants', models.IntegerField(default=0)),
                ('new_sessions', models.IntegerField(default=0)),
                ('messages_sent', models.IntegerField(default=0)),
                ('human_messages', models.IntegerField(default=0)),
                ('ai_messages', models.IntegerField(default=0)),
                ('channel_stats', models.JSONField(default=dict)),
                ('avg_session_duration_minutes', models.FloatField(blank=True, null=True)),
                ('avg_messages_per_session', models.FloatField(blank=True, null=True)),
                ('session_completion_rate', models.FloatField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='teams.team')),
            ],
            options={
                'ordering': ['-date'],
                'indexes': [
                    models.Index(fields=['team', 'date'], name='dashboard_d_team_id_48b3a6_idx'),
                    models.Index(fields=['date'], name='dashboard_d_date_16d63e_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='DashboardFilter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('filter_name', models.CharField(max_length=100)),
                ('filter_data', models.JSONField()),
                ('is_default', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='teams.team')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [
                    models.Index(fields=['team', 'user', 'filter_name'], name='dashboard_d_team_id_e58b90_idx'),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name='dashboardmetricsnapshot',
            constraint=models.UniqueConstraint(fields=('team', 'date'), name='unique_snapshot_per_team_date'),
        ),
        migrations.AddConstraint(
            model_name='dashboardfilter',
            constraint=models.UniqueConstraint(fields=('team', 'user', 'filter_name'), name='unique_filter_per_user_team'),
        ),
        migrations.AddConstraint(
            model_name='dashboardcache',
            constraint=models.UniqueConstraint(fields=('team', 'cache_key'), name='unique_cache_per_team_key'),
        ),
    ]