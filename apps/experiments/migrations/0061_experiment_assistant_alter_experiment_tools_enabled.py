# Generated by Django 4.2.7 on 2024-01-26 12:55

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("assistants", "0001_initial"),
        ("experiments", "0060_alter_experiment_tools_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="experiment",
            name="assistant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="assistants.openaiassistant",
                verbose_name="OpenAI Assistant",
            ),
        ),
        migrations.AlterField(
            model_name="experiment",
            name="tools_enabled",
            field=models.BooleanField(
                default=False,
                help_text="If checked, this bot will be able to use prebuilt tools (set reminders etc). This uses more tokens, so it will cost more. This doesn't currently work with Anthropic models.",
            ),
        ),
    ]
