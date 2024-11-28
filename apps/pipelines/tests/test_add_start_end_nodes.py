from apps.pipelines.migrations.utils.migrate_start_end_nodes import (
    add_missing_start_end_nodes,
    remove_all_start_end_nodes,
)
from apps.pipelines.models import Node, Pipeline
from apps.pipelines.nodes.nodes import EndNode, StartNode
from apps.pipelines.tests.utils import end_node, passthrough_node, start_node
from apps.utils.pytest import django_db_transactional


@django_db_transactional()
def test_empty_pipeline_gets_start_end_nodes(team):
    pipeline = Pipeline.objects.create(team=team, data={"nodes": [], "edges": []})
    pipeline.set_nodes()

    add_missing_start_end_nodes(pipeline, Node)

    assert pipeline.node_set.all().count() == 2
    assert pipeline.node_set.filter(type=StartNode.__name__).exists()
    assert pipeline.node_set.filter(type=EndNode.__name__).exists()


@django_db_transactional()
def test_compliant_pipeline_not_modified(team):
    start = start_node()
    end = end_node()
    passthrough = passthrough_node()
    pipeline = Pipeline.objects.create(
        team=team,
        data={
            "nodes": [
                {"id": start["id"], "data": start},
                {"id": passthrough["id"], "data": passthrough},
                {"id": end["id"], "data": end},
            ],
            "edges": [],
        },
    )
    pipeline.set_nodes()
    add_missing_start_end_nodes(pipeline, Node)

    assert pipeline.node_set.all().count() == 3
    assert pipeline.node_set.get(type=StartNode.__name__).flow_id == start["id"]
    assert pipeline.node_set.get(type=EndNode.__name__).flow_id == end["id"]


@django_db_transactional()
def test_pipeline_gets_start_end_nodes_with_edges(team):
    passthrough_1 = passthrough_node()
    passthrough_2 = passthrough_node()
    pipeline = Pipeline.objects.create(
        team=team,
        data={
            "nodes": [
                {"id": passthrough_1["id"], "data": passthrough_1},
                {"id": passthrough_2["id"], "data": passthrough_2},
            ],
            "edges": [
                {
                    "id": "1->2",
                    "source": passthrough_1["id"],
                    "target": passthrough_2["id"],
                    "sourceHandle": "output",
                    "targetHandle": "input",
                }
            ],
        },
    )
    pipeline.set_nodes()

    add_missing_start_end_nodes(pipeline, Node)

    assert pipeline.node_set.all().count() == 4
    assert pipeline.node_set.filter(type=StartNode.__name__).exists()
    assert pipeline.node_set.filter(type=EndNode.__name__).exists()
    assert len(pipeline.data["edges"]) == 3


@django_db_transactional()
def test_remove_start_end_nodes(team):
    start = start_node()
    end = end_node()
    passthrough_1 = passthrough_node()
    passthrough_2 = passthrough_node()
    pipeline = Pipeline.objects.create(
        team=team,
        data={
            "nodes": [
                {"id": start["id"], "data": start},
                {"id": passthrough_1["id"], "data": passthrough_1},
                {"id": passthrough_2["id"], "data": passthrough_2},
                {"id": end["id"], "data": end},
            ],
            "edges": [
                {
                    "id": "start->1",
                    "source": start["id"],
                    "target": passthrough_1["id"],
                    "sourceHandle": "output",
                    "targetHandle": "input",
                },
                {
                    "id": "1->2",
                    "source": passthrough_1["id"],
                    "target": passthrough_2["id"],
                    "sourceHandle": "output",
                    "targetHandle": "input",
                },
                {
                    "id": "2->end",
                    "source": passthrough_2["id"],
                    "target": end["id"],
                    "sourceHandle": "output",
                    "targetHandle": "input",
                },
            ],
        },
    )
    pipeline.set_nodes()

    remove_all_start_end_nodes(Node)
    pipeline.refresh_from_db()

    assert pipeline.node_set.all().count() == 2
    assert pipeline.node_set.all()[0].flow_id == passthrough_1["id"]
    assert pipeline.node_set.all()[1].flow_id == passthrough_2["id"]
    assert len(pipeline.data["edges"]) == 1
    assert pipeline.data["edges"][0] == {
        "id": "1->2",
        "source": passthrough_1["id"],
        "target": passthrough_2["id"],
        "sourceHandle": "output",
        "targetHandle": "input",
    }
