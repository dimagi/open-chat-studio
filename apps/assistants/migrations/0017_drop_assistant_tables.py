from django.db import migrations

APP_LABEL = "assistants"


def delete_content_types_and_permissions(apps, schema_editor):
    """Drop the app's content types and their permissions, which post_migrate no longer recreates."""
    content_type_model = apps.get_model("contenttypes", "ContentType")
    permission_model = apps.get_model("auth", "Permission")

    content_type_ids = list(content_type_model.objects.filter(app_label=APP_LABEL).values_list("id", flat=True))
    if not content_type_ids:
        return

    permission_model.objects.filter(content_type_id__in=content_type_ids).delete()
    content_type_model.objects.filter(id__in=content_type_ids).delete()


class Migration(migrations.Migration):
    """Drop the tables 0016 emptied.

    The inbound FK columns go first, in pipelines/0032 and custom_actions/0008, or DROP TABLE hits
    their constraints. The content types outlive DeleteModel unless something removes them, and
    removing them any earlier only has post_migrate recreate them for a model still in state.
    """

    dependencies = [
        ("assistants", "0016_delete_assistant_data"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("custom_actions", "0008_drop_operation_assistant_column"),
        ("pipelines", "0034_drop_node_assistant_column"),
    ]

    operations = [
        migrations.RemoveField(model_name="toolresources", name="assistant"),
        migrations.RemoveField(model_name="toolresources", name="files"),
        migrations.DeleteModel(name="ToolResources"),
        migrations.DeleteModel(name="OpenAiAssistant"),
        migrations.RunPython(delete_content_types_and_permissions, migrations.RunPython.noop),
    ]
