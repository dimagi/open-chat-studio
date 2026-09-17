from django.db import migrations

PARAM_KEY = "assistant_id"


def delete_assistant_data(apps, schema_editor):
    """Delete every assistant and strip the mirrored id out of stored node params."""
    apps.get_model("assistants", "OpenAiAssistant").objects.all().delete()

    node_model = apps.get_model("pipelines", "Node")
    stripped = []
    for node in node_model.objects.filter(params__has_key=PARAM_KEY).only("id", "params"):
        del node.params[PARAM_KEY]
        stripped.append(node)
    if stripped:
        node_model.objects.bulk_update(stripped, ["params"], batch_size=500)


class Migration(migrations.Migration):
    """Delete the assistant data ahead of the model removal.

    The experiments dependency is load-bearing rather than ordering hygiene: without it the delete
    cascade resolves against a state that still has Experiment.assistant and updates a column
    0121 already dropped.

    Retiring the assistant file purpose is deliberately not wired in here. It needs live models to
    let django_cleanup reclaim storage, and a management command run mid-graph reads a database
    that is only partly migrated. It ships as retire_assistant_file_purpose, run after deploy, as
    cleanup_orphaned_files already is.
    """

    dependencies = [
        ("assistants", "0015_notify_openai_assistant_removal"),
        ("custom_actions", "0006_remove_customactionoperation_experiment_and_more"),
        ("experiments", "0121_remove_experiment_assistant"),
        ("files", "0016_filechunkembedding_search_vector_index"),
        ("pipelines", "0030_strip_node_data"),
    ]

    operations = [
        migrations.RunPython(delete_assistant_data, migrations.RunPython.noop),
    ]
