from django.db import migrations
from waffle.utils import get_cache

from apps.teams.models import Flag as LiveFlag

NEUTRALISED_FIELDS = {
    "superusers": False,
    "staff": False,
    "authenticated": False,
    "testing": False,
    "rollout": False,
    "percent": None,
    "languages": "",
}


def reduce_flag_inputs_to_everyone_and_teams(apps, schema_editor):
    """A flag decision is `everyone` or `teams`. The other waffle inputs only apply on
    request paths, which team-scoped flag checks don't reliably have, so their stored
    values are reset to inert defaults. `everyone=False` historically meant "no global
    override, use teams", so it becomes `None` before the tri-state gives `False` its
    hard-off meaning. `teams` is left untouched, and the reverse is a noop because the old
    values are deliberately unrecoverable.

    Waffle caches whole Flag instances, so each row's cache keys are flushed or a stale
    copy (notably `superusers=True`, waffle's field default) would keep answering flag
    checks until its TTL. The flush keys derive from the flag name alone, which is why an
    unsaved live-model instance can compute them for the historical rows.
    """
    flag_model = apps.get_model("teams", "Flag")
    flush_keys = set()
    for flag in flag_model.objects.all():
        if flag.everyone is False:
            flag.everyone = None
        for field, value in NEUTRALISED_FIELDS.items():
            setattr(flag, field, value)
        flag.save(update_fields=["everyone", *NEUTRALISED_FIELDS])
        flag.users.clear()
        flag.groups.clear()
        flush_keys.update(LiveFlag(name=flag.name).get_flush_keys())
    if flush_keys:
        get_cache().delete_many(flush_keys)


class Migration(migrations.Migration):
    dependencies = [
        ("teams", "0015_team_created_by"),
    ]

    operations = [
        migrations.RunPython(reduce_flag_inputs_to_everyone_and_teams, migrations.RunPython.noop, elidable=True),
    ]
