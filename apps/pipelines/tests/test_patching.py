import json

import pydantic
import pytest
from django.test import Client
from django.urls import reverse

from apps.pipelines.flow import (
    EdgeDiff,
    FlowEdge,
    FlowNode,
    FlowNodeData,
    NodeDiff,
    PipelineDiffPayload,
)
from apps.pipelines.nodes.nodes import EndNode, LLMResponseWithPrompt, StartNode
from apps.pipelines.patching import apply_pipeline_patch
from apps.utils.factories.pipelines import PipelineFactory

# ──────────────────────────────────────────────
#  PipelineDiffPayload model validation
# ──────────────────────────────────────────────


class TestPipelineDiffPayloadValidation:
    def test_valid_payload(self):
        payload = PipelineDiffPayload(
            base_revision=0,
            nodes=NodeDiff(
                add=[make_flow_node("node-1", "LLMResponseWithPrompt")],
                update=[make_flow_node("node-2", "Passthrough")],
                delete=["node-3"],
            ),
            edges=EdgeDiff(
                add=[make_flow_edge("e1", "node-1", "node-2")],
                delete=["e2"],
            ),
        )
        assert payload.base_revision == 0
        assert len(payload.nodes.add) == 1
        assert len(payload.nodes.update) == 1
        assert len(payload.nodes.delete) == 1
        assert len(payload.edges.add) == 1
        assert len(payload.edges.delete) == 1

    def test_empty_diff_is_valid(self):
        payload = PipelineDiffPayload(base_revision=0)
        assert payload.nodes.add == []
        assert payload.nodes.update == []
        assert payload.nodes.delete == []
        assert payload.edges.add == []
        assert payload.edges.update == []
        assert payload.edges.delete == []

    def test_name_optional(self):
        payload = PipelineDiffPayload(base_revision=0, name="New Name")
        assert payload.name == "New Name"

    def test_rejects_invalid_flow_node(self):
        with pytest.raises(pydantic.ValidationError):
            PipelineDiffPayload(
                base_revision=0,
                nodes=NodeDiff(add=[{"type": "pipelineNode"}]),  # missing required id
            )

    def test_rejects_invalid_flow_edge(self):
        with pytest.raises(pydantic.ValidationError):
            PipelineDiffPayload(
                base_revision=0,
                edges=EdgeDiff(add=[{"source": "a"}]),  # missing required fields
            )


# ──────────────────────────────────────────────
#  Patch engine (apply_pipeline_patch)
# ──────────────────────────────────────────────


def make_flow_node(node_id: str, node_type: str = "Passthrough", params: dict | None = None) -> FlowNode:
    return FlowNode(
        id=node_id,
        type="pipelineNode",
        position={"x": 0, "y": 0},
        data=FlowNodeData(id=node_id, type=node_type, label=node_type, params=params or {"name": node_id}),
    )


def make_flow_edge(edge_id: str, source: str, target: str) -> FlowEdge:
    return FlowEdge(id=edge_id, source=source, target=target)


class TestApplyPipelinePatch:
    def _sample_graph(self):
        return {
            "nodes": [
                {"id": "start", "type": "startNode", "data": {"id": "start", "type": StartNode.__name__}},
                {
                    "id": "llm-1",
                    "type": "pipelineNode",
                    "position": {"x": 100, "y": 0},
                    "data": {"id": "llm-1", "type": LLMResponseWithPrompt.__name__, "label": "LLM", "params": {}},
                },
                {"id": "end", "type": "endNode", "data": {"id": "end", "type": EndNode.__name__}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "llm-1", "sourceHandle": "output", "targetHandle": "input"},
                {"id": "e2", "source": "llm-1", "target": "end", "sourceHandle": "output", "targetHandle": "input"},
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }

    def test_add_node(self):
        graph = self._sample_graph()
        new_node = make_flow_node("llm-2", LLMResponseWithPrompt.__name__)
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(add=[new_node]))
        layout, node_data = apply_pipeline_patch(graph, patch)
        assert len(layout["nodes"]) == 4
        node_ids = {n["id"] for n in layout["nodes"]}
        assert "llm-2" in node_ids
        assert node_data["llm-2"]["type"] == LLMResponseWithPrompt.__name__

    def test_delete_node_removes_connected_edges(self):
        graph = self._sample_graph()
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(delete=["llm-1"]))
        layout, _ = apply_pipeline_patch(graph, patch)
        assert len(layout["nodes"]) == 2
        assert len(layout["edges"]) == 0  # both edges connected to llm-1 are removed

    def test_update_node_params(self):
        graph = self._sample_graph()
        updated = make_flow_node(
            "llm-1", LLMResponseWithPrompt.__name__, params={"prompt": "You are a helpful assistant."}
        )
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(update=[updated]))
        _, node_data = apply_pipeline_patch(graph, patch)
        assert node_data["llm-1"]["params"]["prompt"] == "You are a helpful assistant."

    def test_update_node_position(self):
        graph = self._sample_graph()
        updated = make_flow_node("llm-1", LLMResponseWithPrompt.__name__)
        updated.position = {"x": 999, "y": 888}
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(update=[updated]))
        layout, _ = apply_pipeline_patch(graph, patch)
        updated_result = next(n for n in layout["nodes"] if n["id"] == "llm-1")
        assert updated_result["position"] == {"x": 999, "y": 888}

    def test_add_edge(self):
        graph = self._sample_graph()
        new_edge = make_flow_edge("e3", "start", "end")
        patch = PipelineDiffPayload(base_revision=0, edges=EdgeDiff(add=[new_edge]))
        layout, _ = apply_pipeline_patch(graph, patch)
        assert len(layout["edges"]) == 3

    def test_delete_edge(self):
        graph = self._sample_graph()
        patch = PipelineDiffPayload(base_revision=0, edges=EdgeDiff(delete=["e1"]))
        layout, _ = apply_pipeline_patch(graph, patch)
        assert len(layout["edges"]) == 1
        assert layout["edges"][0]["id"] == "e2"

    def test_unmodified_nodes_preserved(self):
        graph = self._sample_graph()
        new_node = make_flow_node("llm-2", LLMResponseWithPrompt.__name__)
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(add=[new_node]))
        layout, _ = apply_pipeline_patch(graph, patch)
        start = next(n for n in layout["nodes"] if n["id"] == "start")
        assert start["type"] == "startNode"

    def test_viewport_preserved(self):
        graph = self._sample_graph()
        patch = PipelineDiffPayload(base_revision=0)
        layout, _ = apply_pipeline_patch(graph, patch)
        assert layout["viewport"] == {"x": 0, "y": 0, "zoom": 1}

    def test_no_op_patch(self):
        graph = self._sample_graph()
        patch = PipelineDiffPayload(base_revision=0)
        layout, _ = apply_pipeline_patch(graph, patch)
        assert len(layout["nodes"]) == len(graph["nodes"])
        assert len(layout["edges"]) == len(graph["edges"])

    def test_duplicate_add_is_idempotent(self):
        graph = self._sample_graph()
        new_node = make_flow_node("llm-2", LLMResponseWithPrompt.__name__)
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(add=[new_node, new_node]))
        layout, _ = apply_pipeline_patch(graph, patch)
        assert len([n for n in layout["nodes"] if n["id"] == "llm-2"]) == 1

    def test_duplicate_add_emits_no_node_content(self):
        """An add for an id already in the graph is skipped, so its content must not
        reach the Node rows either — a retried add cannot mutate the existing node."""
        graph = self._sample_graph()
        duplicate = make_flow_node("llm-1", LLMResponseWithPrompt.__name__, params={"name": "hijacked"})
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(add=[duplicate]))
        _, node_data = apply_pipeline_patch(graph, patch)
        assert node_data == {}

    def test_delete_then_add_same_id_emits_node_content(self):
        """Deleting an id and re-adding it in the same patch is a genuine replacement,
        so the add's content must be emitted."""
        graph = self._sample_graph()
        replacement = make_flow_node("llm-1", LLMResponseWithPrompt.__name__, params={"name": "replaced"})
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(delete=["llm-1"], add=[replacement]))
        _, node_data = apply_pipeline_patch(graph, patch)
        assert node_data["llm-1"]["params"] == {"name": "replaced"}

    def test_delete_unknown_node(self):
        graph = self._sample_graph()
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(delete=["nonexistent"]))
        layout, _ = apply_pipeline_patch(graph, patch)
        assert len(layout["nodes"]) == 3  # unchanged

    def test_delete_unknown_edge(self):
        graph = self._sample_graph()
        patch = PipelineDiffPayload(base_revision=0, edges=EdgeDiff(delete=["nonexistent"]))
        layout, _ = apply_pipeline_patch(graph, patch)
        assert len(layout["edges"]) == 2  # unchanged

    def test_layout_contains_no_node_content(self):
        """Old-format stored graphs (blobs embedded) come out layout-only."""
        graph = self._sample_graph()
        new_node = make_flow_node("llm-2", LLMResponseWithPrompt.__name__)
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(add=[new_node]))
        layout, _ = apply_pipeline_patch(graph, patch)
        assert all("data" not in n for n in layout["nodes"])

    def test_node_data_covers_only_patched_nodes(self):
        """Stale blobs of unchanged nodes in old-format stored data must not become
        node content updates — only the patch's add/update nodes carry content."""
        graph = self._sample_graph()
        updated = make_flow_node("llm-1", LLMResponseWithPrompt.__name__)
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(update=[updated]))
        _, node_data = apply_pipeline_patch(graph, patch)
        assert set(node_data) == {"llm-1"}

    def test_name_update(self):
        self._sample_graph()
        payload = PipelineDiffPayload(base_revision=0, name="Updated Name")
        # name update is handled by the view, not the patch engine
        # just verify the payload carries it
        assert payload.name == "Updated Name"


# ──────────────────────────────────────────────
#  PATCH endpoint integration tests
# ──────────────────────────────────────────────


@pytest.mark.django_db()
class TestPatchEndpoint:
    @pytest.fixture()
    def pipeline(self, team_with_users):
        pipeline = PipelineFactory.create(team=team_with_users)
        return pipeline

    @pytest.fixture()
    def authed_client(self, team_with_users):
        client = Client()
        user = team_with_users.members.first()
        client.force_login(user)
        return client

    def _patch_url(self, team_slug, pk):
        return reverse("pipelines:pipeline_data", kwargs={"team_slug": team_slug, "pk": pk})

    def test_patch_add_node(self, authed_client, pipeline, team_with_users):
        team_slug = team_with_users.slug
        new_node = {
            "id": "llm-new",
            "type": "pipelineNode",
            "position": {"x": 100, "y": 100},
            "data": {"id": "llm-new", "type": LLMResponseWithPrompt.__name__, "label": "New LLM", "params": {}},
        }
        patch_data = {
            "base_revision": 0,
            "nodes": {
                "add": [new_node],
                "update": [],
                "delete": [],
            },
            "edges": {
                "add": [],
                "update": [],
                "delete": [],
            },
        }
        response = authed_client.patch(
            self._patch_url(team_slug, pipeline.id),
            data=json.dumps(patch_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["edit_revision"] == 1
        assert len(data["data"]["nodes"]) == pipeline.node_set.count()

        # Verify node was created in the database
        pipeline.refresh_from_db()
        assert pipeline.node_set.filter(flow_id="llm-new").exists()

    def test_patch_concurrency_conflict(self, authed_client, pipeline, team_with_users):
        team_slug = team_with_users.slug
        # First save increments revision to 1
        pipeline.edit_revision = 1
        pipeline.save(update_fields=["edit_revision"])

        # Try to patch with stale revision (0 instead of 1)
        patch_data = {
            "base_revision": 0,
            "nodes": {"add": [], "update": [], "delete": []},
            "edges": {"add": [], "update": [], "delete": []},
        }
        response = authed_client.patch(
            self._patch_url(team_slug, pipeline.id),
            data=json.dumps(patch_data),
            content_type="application/json",
        )
        assert response.status_code == 409
        assert "conflict" in response.json()["error"].lower()

    def test_patch_malformed_payload(self, authed_client, pipeline, team_with_users):
        team_slug = team_with_users.slug
        response = authed_client.patch(
            self._patch_url(team_slug, pipeline.id),
            data=json.dumps({"invalid": "data"}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "malformed" in response.json()["error"].lower()

    def test_patch_invalid_graph_still_saves(self, authed_client, pipeline, team_with_users):
        """Invalid drafts must still save — validation errors are returned, not blocking."""
        team_slug = team_with_users.slug
        # Delete the end node to make the graph invalid (disconnected)
        end_node_id = pipeline.node_set.get(type=EndNode.__name__).flow_id

        patch_data = {
            "base_revision": 0,
            "nodes": {
                "add": [],
                "update": [],
                "delete": [end_node_id],
            },
            "edges": {
                "add": [],
                "update": [],
                "delete": [],
            },
        }
        response = authed_client.patch(
            self._patch_url(team_slug, pipeline.id),
            data=json.dumps(patch_data),
            content_type="application/json",
        )
        # Should still return 200 and save
        assert response.status_code == 200
        data = response.json()
        assert data["edit_revision"] == 1
        # The end node was deleted from data
        pipeline.refresh_from_db()
        assert all(n["id"] != end_node_id for n in pipeline.data.get("nodes", []))

    def test_patch_updates_node_params(self, authed_client, pipeline, team_with_users):
        team_slug = team_with_users.slug
        node_id = pipeline.node_set.get(type=EndNode.__name__).flow_id

        updated_node = {
            "id": node_id,
            "type": "endNode",
            "position": {"x": 100, "y": 0},
            "data": {
                "id": node_id,
                "type": EndNode.__name__,
                "label": "Updated end",
                "params": {"name": "the-end"},
            },
        }
        patch_data = {
            "base_revision": 0,
            "nodes": {
                "add": [],
                "update": [updated_node],
                "delete": [],
            },
            "edges": {"add": [], "update": [], "delete": []},
        }
        response = authed_client.patch(
            self._patch_url(team_slug, pipeline.id),
            data=json.dumps(patch_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        pipeline.refresh_from_db()
        updated_node_in_db = pipeline.node_set.get(flow_id=node_id)
        assert updated_node_in_db.params.get("name") == "the-end"
        assert updated_node_in_db.label == "Updated end"

    def test_patch_shadow_writes_position_to_the_row(self, authed_client, pipeline, team_with_users):
        """Pipeline.data stays authoritative for layout, but saves mirror each patched
        node's position onto the row's position columns for the upcoming read switch."""
        team_slug = team_with_users.slug
        node_id = pipeline.node_set.get(type=EndNode.__name__).flow_id
        updated_node = {
            "id": node_id,
            "type": "endNode",
            "position": {"x": 123.5, "y": 45},
            "data": {"id": node_id, "type": EndNode.__name__, "label": "", "params": {"name": "end"}},
        }
        patch_data = {
            "base_revision": 0,
            "nodes": {"add": [], "update": [updated_node], "delete": []},
            "edges": {"add": [], "update": [], "delete": []},
        }
        response = authed_client.patch(
            self._patch_url(team_slug, pipeline.id),
            data=json.dumps(patch_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        row = pipeline.node_set.get(flow_id=node_id)
        assert row.position == {"x": 123.5, "y": 45}

    def test_patch_add_node_without_content_is_a_client_error(self, authed_client, pipeline, team_with_users):
        """FlowNode.data is optional at the wire level; an added node without it (and
        without an existing row) is the client's mistake, not a server error."""
        team_slug = team_with_users.slug
        patch_data = {
            "base_revision": 0,
            "nodes": {"add": [{"id": "contentless", "type": "pipelineNode"}], "update": [], "delete": []},
            "edges": {"add": [], "update": [], "delete": []},
        }
        response = authed_client.patch(
            self._patch_url(team_slug, pipeline.id),
            data=json.dumps(patch_data),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "contentless" in response.json()["error"]
        # nothing was persisted
        pipeline.refresh_from_db()
        assert pipeline.edit_revision == 0
        assert not pipeline.node_set.filter(flow_id="contentless").exists()

    def test_patch_persists_layout_only_data(self, authed_client, pipeline, team_with_users):
        team_slug = team_with_users.slug
        new_node = {
            "id": "llm-new",
            "type": "pipelineNode",
            "position": {"x": 100, "y": 100},
            "data": {"id": "llm-new", "type": LLMResponseWithPrompt.__name__, "label": "New LLM", "params": {}},
        }
        patch_data = {
            "base_revision": 0,
            "nodes": {"add": [new_node], "update": [], "delete": []},
            "edges": {"add": [], "update": [], "delete": []},
        }
        response = authed_client.patch(
            self._patch_url(team_slug, pipeline.id),
            data=json.dumps(patch_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        pipeline.refresh_from_db()
        assert all("data" not in node for node in pipeline.data["nodes"])
        # the response still serves full nodes, reconstructed from the rows
        response_nodes = {n["id"]: n for n in response.json()["data"]["nodes"]}
        assert response_nodes["llm-new"]["data"]["label"] == "New LLM"

    def test_patch_does_not_rewrite_rows_from_stale_stored_blob(self, authed_client, pipeline, team_with_users):
        """Pre-migration rows still embed node content in pipeline.data. Out-of-band row
        edits (set_params, version publish) make that blob stale; a PATCH touching other
        nodes must not push the stale blob back into the rows."""
        team_slug = team_with_users.slug
        start_row = pipeline.node_set.get(type=StartNode.__name__)
        end_row = pipeline.node_set.get(type=EndNode.__name__)
        # simulate an old-format stored graph whose blob disagrees with the row
        pipeline.data = {
            "edges": [],
            "nodes": [
                {
                    "id": start_row.flow_id,
                    "type": "startNode",
                    "data": {"id": start_row.flow_id, "type": StartNode.__name__, "params": {"name": "stale-name"}},
                },
                {"id": end_row.flow_id, "type": "endNode", "data": {"id": end_row.flow_id, "type": EndNode.__name__}},
            ],
        }
        pipeline.save(update_fields=["data"])
        start_row.set_params({"name": "fresh-name"})

        patch_data = {
            "base_revision": 0,
            "nodes": {"add": [], "update": [], "delete": []},
            "edges": {"add": [], "update": [], "delete": []},
        }
        response = authed_client.patch(
            self._patch_url(team_slug, pipeline.id),
            data=json.dumps(patch_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        start_row.refresh_from_db()
        assert start_row.params == {"name": "fresh-name"}


# ──────────────────────────────────────────────
#  POST endpoint backward compatibility
# ──────────────────────────────────────────────


@pytest.mark.django_db()
class TestPostEndpointBackwardCompatibility:
    @pytest.fixture()
    def pipeline(self, team_with_users):
        return PipelineFactory.create(team=team_with_users)

    @pytest.fixture()
    def authed_client(self, team_with_users):
        client = Client()
        user = team_with_users.members.first()
        client.force_login(user)
        return client

    def _post_url(self, team_slug, pk):
        return reverse("pipelines:pipeline_data", kwargs={"team_slug": team_slug, "pk": pk})

    def test_post_full_save_still_works(self, authed_client, pipeline, team_with_users):
        team_slug = team_with_users.slug
        post_data = {
            "name": "Updated Pipeline",
            "data": {
                "nodes": [
                    {"id": "start", "type": "startNode", "data": {"id": "start", "type": StartNode.__name__}},
                    {"id": "end", "type": "endNode", "data": {"id": "end", "type": EndNode.__name__}},
                ],
                "edges": [
                    {"id": "e1", "source": "start", "target": "end"},
                ],
            },
        }
        response = authed_client.post(
            self._post_url(team_slug, pipeline.id),
            data=json.dumps(post_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert "edit_revision" in data
        assert data["edit_revision"] >= 1
        assert len(data["data"]["nodes"]) == 2

    def test_post_increments_revision(self, authed_client, pipeline, team_with_users):
        team_slug = team_with_users.slug
        assert pipeline.edit_revision == 0

        post_data = {
            "name": "Test",
            "data": {
                "nodes": [
                    {"id": "start", "type": "startNode", "data": {"id": "start", "type": StartNode.__name__}},
                    {"id": "end", "type": "endNode", "data": {"id": "end", "type": EndNode.__name__}},
                ],
                "edges": [],
            },
        }
        response = authed_client.post(
            self._post_url(team_slug, pipeline.id),
            data=json.dumps(post_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        pipeline.refresh_from_db()
        assert pipeline.edit_revision == 1

    def test_post_persists_layout_only_data(self, authed_client, pipeline, team_with_users):
        team_slug = team_with_users.slug
        post_data = {
            "name": "Updated Pipeline",
            "data": {
                "nodes": [
                    {
                        "id": "start",
                        "type": "startNode",
                        "data": {"id": "start", "type": StartNode.__name__, "params": {"name": "start"}},
                    },
                    {
                        "id": "end",
                        "type": "endNode",
                        "data": {"id": "end", "type": EndNode.__name__, "params": {"name": "end"}},
                    },
                ],
                "edges": [],
            },
        }
        response = authed_client.post(
            self._post_url(team_slug, pipeline.id),
            data=json.dumps(post_data),
            content_type="application/json",
        )
        assert response.status_code == 200
        pipeline.refresh_from_db()
        assert all("data" not in node for node in pipeline.data["nodes"])
        assert pipeline.node_set.get(flow_id="start").params == {"name": "start"}
        # the response still serves full nodes, reconstructed from the rows
        response_nodes = {n["id"]: n for n in response.json()["data"]["nodes"]}
        assert response_nodes["start"]["data"]["params"] == {"name": "start"}

    def test_get_returns_edit_revision(self, authed_client, pipeline, team_with_users):
        team_slug = team_with_users.slug
        response = authed_client.get(
            self._post_url(team_slug, pipeline.id),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["pipeline"]["edit_revision"] == 0
