from django.db import migrations


class Migration(migrations.Migration):
    """Ran notify_openai_assistant_removal, a command deleted with the feature in #4254.

    Kept as a noop so a fresh database does not try to call it. The original run is recorded in
    the CustomMigration row named notify_openai_assistant_removal.
    """

    dependencies = [
        ("assistants", "0014_alter_toolresources_extra"),
        ("data_migrations", "0001_initial"),
        ("teams", "0014_team_is_migrating"),
    ]

    operations = [
        migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop),
    ]
