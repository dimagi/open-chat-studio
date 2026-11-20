from django.db import migrations

from apps.chat.migrations.utils import fold_message_attachment_ids as fold


def fold_message_attachment_ids(apps, schema_editor):
    fold.fold_message_attachment_ids(apps.get_model("chat", "ChatMessage"), schema_editor.connection)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("chat", "0032_chatmessage_mod_created_idx"),
    ]

    operations = [
        migrations.RunPython(fold_message_attachment_ids, migrations.RunPython.noop),
    ]
