# Generated by Django 4.2.7 on 2024-05-08 06:22

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('teams', '0005_invitation_groups'),
        ('api', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='userapikey',
            name='team',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='api_keys', to='teams.team'),
            preserve_default=False,
        ),
    ]
