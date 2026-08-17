# Generated for issue #4200: admins can temporarily disable a channel.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bot_channels", "0031_notify_widget_version_release_0_11_0"),
    ]

    operations = [
        migrations.AddField(
            model_name="experimentchannel",
            name="enabled",
            field=models.BooleanField(
                default=True,
                help_text="Uncheck to temporarily block access to this bot on this channel.",
            ),
        ),
        migrations.AddField(
            model_name="experimentchannel",
            name="disabled_message",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Optional message sent to anyone who messages this channel while it is disabled. "
                    "Leave blank for the bot to stay silent."
                ),
                verbose_name="Disabled message",
            ),
        ),
    ]
