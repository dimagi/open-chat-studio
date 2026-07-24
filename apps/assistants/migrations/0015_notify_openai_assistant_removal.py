from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("assistants", "0014_alter_toolresources_extra"),
        ("data_migrations", "0001_initial"),
        ("teams", "0014_team_is_migrating"),
    ]

    operations = [
        # Email team admins about the upcoming OpenAI Assistants API removal.
        # force=True because the command's run-once slug is fixed; Django tracks this migration's single run.
        RunDataMigration(
            "notify_openai_assistant_removal",
            command_options={"force": True},
        ),
    ]
