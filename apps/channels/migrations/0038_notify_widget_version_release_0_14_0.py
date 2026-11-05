from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("bot_channels", "0037_notify_widget_version_release_0_13_0"),
        ("data_migrations", "0001_initial"),
        ("teams", "0012_team_metadata"),
    ]

    operations = [
        RunDataMigration(
            "notify_widget_version_release",
            command_options={
                "force": True,
                "widget_version": "0.14.0",
                "notes": (
                    'New: the widget has a dark theme. Set the theme attribute to "dark" to use it; '
                    'embedded widgets default to "light", so existing embeds are unchanged. '
                    "New: files the chatbot attaches to a message can be downloaded from the chat. "
                    "Changed: a participant on the team's block list now sees a generic send error "
                    "instead of the session ending."
                ),
            },
        ),
    ]
