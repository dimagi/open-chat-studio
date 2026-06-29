from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("bot_channels", "0027_notify_widget_deprecation_below_0_6_0"),
        ("data_migrations", "0001_initial"),
        # The command loads the live Team model (all columns), so the schema
        # for those fields must be in place before it runs.
        ("teams", "0012_team_metadata"),
    ]

    operations = [
        # Announce the 0.10.0 widget release to every team with an embedded-widget
        # channel. force=True because the command's run-once slug is fixed; Django
        # tracks this migration's single run. See docs/developer_guides/widget_versioning.md
        RunDataMigration(
            "notify_widget_version_release",
            command_options={
                "force": True,
                "widget_version": "0.10.0",
                "notes": (
                    "Add a public JavaScript event API. The widget now dispatches lifecycle events so you can react to them with addEventListener."
                ),
                "changelog_url": "https://docs.openchatstudio.com/chat_widget/changelog/#v0100-2026-06-26",
            },
        ),
    ]
