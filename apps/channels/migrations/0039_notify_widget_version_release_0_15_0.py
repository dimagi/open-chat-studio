from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("bot_channels", "0038_notify_widget_version_release_0_14_0"),
        ("data_migrations", "0001_initial"),
        ("teams", "0012_team_metadata"),
    ]

    operations = [
        RunDataMigration(
            "notify_widget_version_release",
            command_options={
                "force": True,
                "widget_version": "0.15.0",
                "changelog_url": "https://docs.openchatstudio.com/chat_widget/changelog/",
                "notes": (
                    "Changed: a participant on the team's block list now sees \"You have been blocked "
                    'from using this chatbot." in the chat window instead of a generic error, and the '
                    "message box is disabled."
                ),
            },
        ),
    ]
