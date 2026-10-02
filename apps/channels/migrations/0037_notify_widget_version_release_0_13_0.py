from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("bot_channels", "0036_alter_experimentchannel_session_token_lifetime"),
        ("data_migrations", "0001_initial"),
        ("teams", "0012_team_metadata"),
    ]

    operations = [
        # Announce the 0.13.0 widget release to every team with an embedded-widget
        # channel. force=True because the command's run-once slug is fixed; Django
        # tracks this migration's single run. See docs/developer_guides/widget_versioning.md
        RunDataMigration(
            "notify_widget_version_release",
            command_options={
                "force": True,
                "widget_version": "0.13.0",
                "changelog_url": "https://docs.openchatstudio.com/chat_widget/changelog/#v0130-2026-09-16",
                "notes": (
                    "New: a chatbot's consent form is collected in the widget — the participant is asked "
                    "when they send their first message, and accepting sends what they already typed. "
                    "New: on channels using OAuth credential mode, the widget renews the session token in "
                    "the background through your authTokenProvider, so chats and uploads carry on past the "
                    "token's lifetime instead of expiring. "
                    "Changed: messages typed by the participant render as plain text, so the "
                    "--code-bg-user-color, --code-border-user-color and --code-text-user-color CSS "
                    "properties no longer have any effect. Bot and welcome messages still render markdown."
                ),
            },
        ),
    ]
