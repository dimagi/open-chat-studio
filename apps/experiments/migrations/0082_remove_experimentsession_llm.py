# Generated by Django 4.2.11 on 2024-07-04 18:32

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0081_rename_public_id_experimentsession_external_id_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="experimentsession",
            name="llm",
        ),
    ]
