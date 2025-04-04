from unittest.mock import MagicMock
from uuid import uuid4

import pytest

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
    pipeline.update_nodes_from_data()

    add_missing_start_end_nodes(pipeline, Node)

    assert pipeline.node_set.all().count() == 2
    assert pipeline.node_set.filter(type=StartNode.__name__).exists()
    assert pipeline.node_set.filter(type=EndNode.__name__).exists()


@django_db_transactional()
def test_recursive_pipeline_has_start_end_nodes(team):
    passthrough = passthrough_node()
    pipeline = Pipeline.objects.create(
        team=team,
        data={
            "nodes": [
                {"id": passthrough["id"], "data": passthrough},
            ],
            "edges": [
                {
                    "id": "passthrough->passthrough",
                    "source": passthrough["id"],
                    "target": passthrough["id"],
                    "sourceHandle": "output",
                    "targetHandle": "input",
                },
            ],
        },
    )
    pipeline.update_nodes_from_data()
    pipeline.save()
    pipeline.refresh_from_db()

    add_missing_start_end_nodes(pipeline, Node)

    assert pipeline.node_set.all().count() == 3
    assert pipeline.node_set.filter(type=StartNode.__name__).exists()
    assert pipeline.node_set.filter(type=EndNode.__name__).exists()


@django_db_transactional()
def test_dangling_edge_has_start_end_nodes(team):
    passthrough = passthrough_node()
    pipeline = Pipeline.objects.create(
        team=team,
        data={
            "nodes": [
                {"id": passthrough["id"], "data": passthrough},
            ],
            "edges": [
                {
                    "id": "passthrough->passthrough",
                    "source": passthrough["id"],
                    "target": "abcd",
                    "sourceHandle": "output",
                    "targetHandle": "input",
                },
            ],
        },
    )
    pipeline.update_nodes_from_data()
    pipeline.save()
    pipeline.refresh_from_db()

    add_missing_start_end_nodes(pipeline, Node)

    assert pipeline.node_set.all().count() == 3
    assert pipeline.node_set.filter(type=StartNode.__name__).exists()
    assert pipeline.node_set.filter(type=EndNode.__name__).exists()


@django_db_transactional()
def test_sentry_6107296412(team):
    pipeline = Pipeline.objects.create(
        team=team,
        data={
            "edges": [
                {
                    "id": "reactflow__edge-LLMResponseWithPrompt-efcB5output-BooleanNode-j4zkainput",
                    "source": "LLMResponseWithPrompt-efcB5",
                    "target": "BooleanNode-j4zka",
                    "sourceHandle": "output",
                    "targetHandle": "input",
                }
            ],
            "nodes": [
                {
                    "id": "BooleanNode-j4zka",
                    "data": {
                        "id": "BooleanNode-j4zka",
                        "type": "BooleanNode",
                        "label": "Boolean Node",
                        "params": {},
                        "inputParams": [{"name": "input_equals", "type": "<class 'str'>", "default": None}],
                    },
                    "type": "pipelineNode",
                    "position": {"x": 613.296875, "y": 202},
                },
                {
                    "id": "LLMResponseWithPrompt-efcB5",
                    "data": {
                        "id": "LLMResponseWithPrompt-efcB5",
                        "type": "LLMResponseWithPrompt",
                        "label": "LLM",
                        "params": {},
                        "inputParams": [
                            {"name": "llm_provider_id", "type": "LlmProviderId", "default": None},
                            {"name": "llm_model", "type": "LlmModel", "default": None},
                            {"name": "llm_temperature", "type": "LlmTemperature", "default": 1},
                            {"name": "history_type", "type": "HistoryType", "default": "none"},
                            {"name": "history_name", "type": "HistoryName", "default": None},
                            {"name": "max_token_limit", "type": "MaxTokenLimit", "default": 8192},
                            {"name": "source_material_id", "type": "SourceMaterialId", "default": None},
                            {
                                "name": "prompt",
                                "type": "Prompt",
                                "default": "You are a helpful assistant.",
                            },
                        ],
                    },
                    "type": "pipelineNode",
                    "position": {"x": 62.936439804895485, "y": 78.34190341898599},
                },
            ],
            "viewport": {"x": 3.2980612579958346, "y": 17.332422642001603, "zoom": 0.8467453123625279},
        },
    )

    Node.objects.create(
        pipeline=pipeline,
        flow_id="LLMResponseWithPrompt-efcB5",
        label="LLM",
        type="LLMResponseWithPrompt",
        params={},
    )

    add_missing_start_end_nodes(pipeline, Node)
    assert pipeline.node_set.all().count() == 4  # The missing Node is recreated
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
    pipeline.update_nodes_from_data()
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
    pipeline.update_nodes_from_data()

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
    pipeline.update_nodes_from_data()

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


@django_db_transactional()
@pytest.mark.parametrize("version_before_removing_node", [True, False])
def test_remove_nodes(version_before_removing_node, team):
    """
    Nodes that doesn't yet have versions should be deleted whereas nodes with versions should be archived
    """
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
    pipeline.update_nodes_from_data()

    if version_before_removing_node:
        pipeline.create_new_version()

    # User removes passthough node 2
    pipeline.data = {
        "nodes": [
            {"id": start["id"], "data": start},
            {"id": passthrough_1["id"], "data": passthrough_1},
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
                "id": "2->end",
                "source": passthrough_1["id"],
                "target": end["id"],
                "sourceHandle": "output",
                "targetHandle": "input",
            },
        ],
    }
    pipeline.save()
    pipeline.update_nodes_from_data()

    if version_before_removing_node:
        node = Node.objects.get_all().get(flow_id=passthrough_2["id"], working_version_id=None)
        assert node.is_archived is True
        assert node.versions.count() == 1
    else:
        assert Node.objects.get_all().filter(flow_id=passthrough_2["id"]).count() == 0


@django_db_transactional()
def test_pipeline_creation_without_llm(team):
    pipeline = Pipeline.create_default(team=team)

    assert pipeline.name == "New Pipeline 1"
    assert "nodes" in pipeline.data
    assert "edges" in pipeline.data
    assert len(pipeline.data["nodes"]) == 2
    assert len(pipeline.data["edges"]) == 0
    node_types = [node["data"]["type"] for node in pipeline.data["nodes"]]
    assert "StartNode" in node_types
    assert "EndNode" in node_types


@django_db_transactional()
def test_pipeline_creation_with_llm(team):
    mock_llm_provider = MagicMock(id=str(uuid4()))
    mock_llm_model = MagicMock(id=str(uuid4()), max_token_limit=2048)

    pipeline = Pipeline.create_default(
        team=team,
        llm_provider_id=mock_llm_provider.id,
        llm_provider_model=mock_llm_model,
    )

    assert "nodes" in pipeline.data
    assert "edges" in pipeline.data
    assert len(pipeline.data["nodes"]) == 3
    llm_node = next(node for node in pipeline.data["nodes"] if node["data"]["type"] == "LLMResponseWithPrompt")
    params = llm_node["data"]["params"]
    assert params["llm_provider_id"] == mock_llm_provider.id
    assert params["llm_provider_model_id"] == mock_llm_model.id
    assert params["llm_temperature"] == 0.7
    assert params["user_max_token_limit"] == mock_llm_model.max_token_limit


@django_db_transactional()
def test_pipeline_edge_connections(team):
    mock_llm_provider = MagicMock(id=str(uuid4()))
    mock_llm_model = MagicMock(id=str(uuid4()), max_token_limit=2048)

    pipeline = Pipeline.create_default(
        team=team,
        llm_provider_id=mock_llm_provider.id,
        llm_provider_model=mock_llm_model,
    )

    edges = pipeline.data["edges"]
    assert len(edges) == 2
    start_to_llm = edges[0]
    llm_to_end = edges[1]
    assert start_to_llm["sourceHandle"] == "output"
    assert start_to_llm["targetHandle"] == "input"
    assert llm_to_end["sourceHandle"] == "output"
    assert llm_to_end["targetHandle"] == "input"
