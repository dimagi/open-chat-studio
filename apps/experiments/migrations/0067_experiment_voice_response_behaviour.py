# Generated by Django 4.2.7 on 2024-02-21 15:01

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0066_alter_experimentsession_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="experiment",
            name="voice_response_behaviour",
            field=models.CharField(
                choices=[
                    ("always", "Always"),
                    ("reciprocal", "Reciprocal"),
                    ("never", "Never"),
                ],
                default="reciprocal",
                help_text="This tells the bot when to reply with voice messages",
                max_length=10,
            ),
        ),
    ]
