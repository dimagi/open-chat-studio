# Generated by Django 4.2.7 on 2024-02-02 09:44

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0008_chatmessage_summary_content"),
    ]

    operations = [
        migrations.AddField(
            model_name="chat",
            name="metadata",
            field=models.JSONField(default=dict),
        ),
    ]
