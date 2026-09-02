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


def convert_everyone_false_to_none(apps, schema_editor):
    """`everyone=False` historically meant "no global override, use teams"; the tri-state
    admin gives `False` a hard-off meaning, so stored `False` values become `None`.

    Rows created since 0016 carry waffle's field defaults (notably `superusers=True`), so
    the request-only neutralisation runs again on every row. The reverse is a noop because
    the old values are deliberately unrecoverable. Cache flushing works as in 0016: flush
    keys derive from the flag name alone, so an unsaved live-model instance can compute
    them for the historical rows.
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
        ("teams", "0016_neutralise_request_only_flag_fields"),
    ]

    operations = [
        migrations.RunPython(convert_everyone_false_to_none, migrations.RunPython.noop, elidable=True),
    ]
