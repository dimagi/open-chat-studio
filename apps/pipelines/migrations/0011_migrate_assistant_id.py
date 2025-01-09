from django.db import migrations


def _update_assistant_id_type(apps, schema_editor):
    Node = apps.get_model("pipelines", "Node")

    for node in Node.objects.filter(type="AssistantNode").all():
        if assistant_id := node.params.get("assistant_id"):
            if isinstance(assistant_id, str):
                continue

            node.params["assistant_id"] = str(assistant_id)
            node.save()


class Migration(migrations.Migration):

    dependencies = [
        ('pipelines', '0010_auto_20241127_2042'),
    ]

    operations = [
        migrations.RunPython(_update_assistant_id_type, reverse_code=migrations.RunPython.noop)
    ]
