# Generated by Django 4.2.7 on 2024-02-13 15:02

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('files', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL("UPDATE files_file SET external_source = 'openai' WHERE external_id != ''", migrations.RunSQL.noop),
    ]