from django.db import migrations

from apps.data_migrations.utils.migrations import RunDataMigration


class Migration(migrations.Migration):
    dependencies = [
        ("bot_channels", "0026_experimentchannel_widget_version_and_more"),
        ("data_migrations", "0001_initial"),
    ]

    operations = [
        # Notify teams running widget versions deprecated by the < 0.6.0 entry
        # in apps/channels/widget_versions.py. force=True because the command's
        # run-once slug is fixed; Django tracks this migration's single run.
        RunDataMigration("notify_deprecated_widget_versions", command_options={"force": True}),
    ]
