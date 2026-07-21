# Generated for issue #3858: durable per-channel widget auth policy.

from django.db import migrations, models
from packaging.version import InvalidVersion, Version

# Kept in sync with apps.channels.models.WidgetAuthLevel. Duplicated here so the
# migration is not coupled to future changes in the enum.
LEVEL_NONE = 0
LEVEL_EMBED_KEY = 1
LEVEL_SESSION_TOKEN = 2

# Widgets older than 0.5.1 report this placeholder (they send no version header).
UNKNOWN_WIDGET_VERSION = "unknown"

EMBED_KEY_INTRODUCED = Version("0.5.1")
SESSION_TOKEN_INTRODUCED = Version("0.9.0")


def _level_for_version(widget_version):
    """Map a recorded widget version to the auth level it can satisfy.

    - "unknown" / unparseable / < 0.5.1  -> NONE (predates the embed key)
    - 0.5.1 <= v < 0.9.0                  -> EMBED_KEY (sends embed key, no token)
    - >= 0.9.0 / null (never connected)   -> SESSION_TOKEN (full token flow / treat as new)
    """
    if widget_version is None:
        return LEVEL_SESSION_TOKEN
    if widget_version == UNKNOWN_WIDGET_VERSION:
        return LEVEL_NONE
    try:
        parsed = Version(widget_version)
    except InvalidVersion:
        return LEVEL_NONE
    if parsed < EMBED_KEY_INTRODUCED:
        return LEVEL_NONE
    if parsed < SESSION_TOKEN_INTRODUCED:
        return LEVEL_EMBED_KEY
    return LEVEL_SESSION_TOKEN


def set_auth_levels(apps, schema_editor):
    ExperimentChannel = apps.get_model("bot_channels", "ExperimentChannel")
    # Only EMBEDDED_WIDGET channels are grandfathered; everything else keeps the
    # SESSION_TOKEN default set by the schema migration.
    channels = ExperimentChannel.objects.filter(platform="embedded_widget")
    for channel in channels.iterator():
        level = _level_for_version(channel.widget_version)
        if level != LEVEL_SESSION_TOKEN:
            ExperimentChannel.objects.filter(pk=channel.pk).update(required_auth_level=level)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("bot_channels", "0028_notify_widget_version_release_0_10_0"),
    ]

    operations = [
        migrations.AddField(
            model_name="experimentchannel",
            name="required_auth_level",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (0, "None (pre-0.5.1 legacy)"),
                    (1, "Embed key only (0.5.1 – 0.8.x)"),
                    (2, "Session token required (0.9.0+)"),
                ],
                default=2,
                help_text=(
                    "Minimum authentication an embedded widget must provide. New widgets (0.9.0+) send a "
                    "session token; only downgrade this for a channel you know is running an older widget."
                ),
            ),
        ),
        migrations.RunPython(set_auth_levels, noop),
    ]
