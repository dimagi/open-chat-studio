from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("bot_channels", "0033_experimentchannel_credential_mode_and_more"),
        ("data_migrations", "0001_initial"),
        ("teams", "0012_team_metadata"),
    ]

    operations = [
        # Announce the 0.12.0 widget release to every team with an embedded-widget
        # channel. force=True because the command's run-once slug is fixed; Django
        # tracks this migration's single run. See docs/developer_guides/widget_versioning.md
        RunDataMigration(
            "notify_widget_version_release",
            command_options={
                "force": True,
                "widget_version": "0.12.0",
                "changelog_url": "https://docs.openchatstudio.com/chat_widget/changelog/#v0120-2026-08-27",
                "notes": (
                    'New: persistent-session="tab" keeps a conversation across reloads but clears it '
                    "when the tab closes. "
                    "New: kiosk mode offers a restart once a session has ended. "
                    "New: auth-token-provider supplies an OAuth bearer token for chatbots whose channel "
                    "requires one. "
                    "Changed: the device time zone is sent on session start, and .xlsm attachments are "
                    "accepted while .bmp and .svg are not."
                ),
            },
        ),
    ]
