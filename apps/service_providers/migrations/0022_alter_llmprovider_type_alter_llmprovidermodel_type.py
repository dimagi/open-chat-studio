# Generated by Django 5.1.2 on 2024-11-14 16:52

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("service_providers", "0021_migrate_llm_models"),
    ]

    operations = [
        migrations.AlterField(
            model_name="llmprovider",
            name="type",
            field=models.CharField(
                choices=[
                    ("openai", "OpenAI"),
                    ("azure", "Azure OpenAI"),
                    ("anthropic", "Anthropic"),
                    ("groq", "Groq"),
                    ("perplexity", "Perplexity"),
                ],
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="llmprovidermodel",
            name="type",
            field=models.CharField(
                choices=[
                    ("openai", "OpenAI"),
                    ("azure", "Azure OpenAI"),
                    ("anthropic", "Anthropic"),
                    ("groq", "Groq"),
                    ("perplexity", "Perplexity"),
                ],
                max_length=255,
            ),
        ),
    ]