from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("bot_channels", "0030_widget_auth_level_ratchet_fields"),
        ("data_migrations", "0001_initial"),
        ("teams", "0012_team_metadata"),
    ]

    operations = [
        # Announce the 0.11.0 widget release to every team with an embedded-widget
        # channel. force=True because the command's run-once slug is fixed; Django
        # tracks this migration's single run. See docs/developer_guides/widget_versioning.md
        RunDataMigration(
            "notify_widget_version_release",
            command_options={
                "force": True,
                "widget_version": "0.11.0",
                "notes": (
                    "Maintenance release: removes the deprecated use_session_token request field. "
                    "Session token behaviour is now fully governed by the channel's auth level — no widget changes required."
                ),
            },
        ),
    ]
