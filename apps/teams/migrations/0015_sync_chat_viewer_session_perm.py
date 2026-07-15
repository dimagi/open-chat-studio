"""Sync default group permissions so the 'Chat Viewer' group gains
``experiments.view_experimentsession``.

Chat Viewer previously only had chat/annotations/files view permissions, which was
not enough to reach any session list (the All Sessions page and per-chatbot session
tables require ``experiments.view_experimentsession``). Without a session list there
was no UI path to transcripts, so the role was effectively unusable in isolation.

Permission changes are normally applied by ``create_default_groups()`` on every deploy
(see ``apps/web/management/commands/migrate.py``). This migration re-runs that sync so the
new permission is applied deterministically as part of the migration history, matching the
behaviour of a fresh deploy. It depends on the latest experiments migration to guarantee the
``experimentsession`` content type and its permissions already exist.
"""

from django.db import migrations

from apps.teams.backends import create_default_groups


def sync_groups(apps, schema_editor):
    create_default_groups()


class Migration(migrations.Migration):
    dependencies = [
        ("teams", "0014_team_is_migrating"),
        ("experiments", "0147_drop_survey"),
    ]

    operations = [
        migrations.RunPython(sync_groups, migrations.RunPython.noop),
    ]
