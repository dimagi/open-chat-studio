# Generated by Django 4.2.14 on 2024-08-02 00:39

from django.db import migrations

from apps.pipelines.flow import Flow


def _migrate_nodes(apps, schema_editor):
    Pipeline = apps.get_model("pipelines", "Pipeline")
    Node = apps.get_model("pipelines", "Node")
    for pipeline in Pipeline.objects.all():
        flow = Flow(**pipeline.data)
        for node in flow.nodes:
            node_object, _ = Node.objects.get_or_create(pipeline=pipeline, flow_id=node.id)
            node_object.type = node.data.type
            node_object.params = node.data.params
            node_object.label = node.data.label
            node_object.save()


def _delete_all_nodes(apps, schema_editor):
    Node = apps.get_model("pipelines", "Node")
    Node.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("pipelines", "0004_node"),
    ]

    operations = [migrations.RunPython(_migrate_nodes, reverse_code=_delete_all_nodes)]
