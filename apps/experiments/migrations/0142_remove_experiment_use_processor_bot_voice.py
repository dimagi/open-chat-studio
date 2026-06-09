from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("experiments", "0141_experimentsession_session_token_required"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="experiment",
            name="use_processor_bot_voice",
        ),
    ]
