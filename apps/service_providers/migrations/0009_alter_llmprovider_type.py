# Generated by Django 4.2.7 on 2024-01-26 15:35

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0008_messagingprovider"),
    ]

    operations = [
        migrations.AlterField(
            model_name="llmprovider",
            name="type",
            field=models.CharField(
                choices=[("openai", "OpenAI"), ("azure", "Azure OpenAI"), ("anthropic", "Anthropic")], max_length=255
            ),
        ),
    ]
