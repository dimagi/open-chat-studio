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
                nodes=NodeDiff(add=[{"id": "n1"}]),  # missing required fields
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


def make_flow_node(node_id: str, node_type: str = "Passthrough") -> FlowNode:
    return FlowNode(
        id=node_id,
        type="pipelineNode",
        position={"x": 0, "y": 0},
        data=FlowNodeData(id=node_id, type=node_type, label=node_type, params={"name": node_id}),
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
        result = apply_pipeline_patch(graph, patch)
        assert len(result["nodes"]) == 4
        node_ids = {n["id"] for n in result["nodes"]}
        assert "llm-2" in node_ids

    def test_delete_node_removes_connected_edges(self):
        graph = self._sample_graph()
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(delete=["llm-1"]))
        result = apply_pipeline_patch(graph, patch)
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 0  # both edges connected to llm-1 are removed

    def test_update_node_params(self):
        graph = self._sample_graph()
        updated = make_flow_node("llm-1", LLMResponseWithPrompt.__name__)
        updated.data.params = {"prompt": "You are a helpful assistant."}
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(update=[updated]))
        result = apply_pipeline_patch(graph, patch)
        updated_result = next(n for n in result["nodes"] if n["id"] == "llm-1")
        assert updated_result["data"]["params"]["prompt"] == "You are a helpful assistant."

    def test_update_node_position(self):
        graph = self._sample_graph()
        updated = make_flow_node("llm-1", LLMResponseWithPrompt.__name__)
        updated.position = {"x": 999, "y": 888}
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(update=[updated]))
        result = apply_pipeline_patch(graph, patch)
        updated_result = next(n for n in result["nodes"] if n["id"] == "llm-1")
        assert updated_result["position"] == {"x": 999, "y": 888}

    def test_add_edge(self):
        graph = self._sample_graph()
        new_edge = make_flow_edge("e3", "start", "end")
        patch = PipelineDiffPayload(base_revision=0, edges=EdgeDiff(add=[new_edge]))
        result = apply_pipeline_patch(graph, patch)
        assert len(result["edges"]) == 3

    def test_delete_edge(self):
        graph = self._sample_graph()
        patch = PipelineDiffPayload(base_revision=0, edges=EdgeDiff(delete=["e1"]))
        result = apply_pipeline_patch(graph, patch)
        assert len(result["edges"]) == 1
        assert result["edges"][0]["id"] == "e2"

    def test_unmodified_nodes_preserved(self):
        graph = self._sample_graph()
        new_node = make_flow_node("llm-2", LLMResponseWithPrompt.__name__)
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(add=[new_node]))
        result = apply_pipeline_patch(graph, patch)
        start = next(n for n in result["nodes"] if n["id"] == "start")
        assert start["type"] == "startNode"

    def test_viewport_preserved(self):
        graph = self._sample_graph()
        patch = PipelineDiffPayload(base_revision=0)
        result = apply_pipeline_patch(graph, patch)
        assert result["viewport"] == {"x": 0, "y": 0, "zoom": 1}

    def test_no_op_patch(self):
        graph = self._sample_graph()
        patch = PipelineDiffPayload(base_revision=0)
        result = apply_pipeline_patch(graph, patch)
        assert len(result["nodes"]) == len(graph["nodes"])
        assert len(result["edges"]) == len(graph["edges"])

    def test_duplicate_add_is_idempotent(self):
        graph = self._sample_graph()
        new_node = make_flow_node("llm-2", LLMResponseWithPrompt.__name__)
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(add=[new_node, new_node]))
        result = apply_pipeline_patch(graph, patch)
        assert len([n for n in result["nodes"] if n["id"] == "llm-2"]) == 1

    def test_delete_unknown_node(self):
        graph = self._sample_graph()
        patch = PipelineDiffPayload(base_revision=0, nodes=NodeDiff(delete=["nonexistent"]))
        result = apply_pipeline_patch(graph, patch)
        assert len(result["nodes"]) == 3  # unchanged

    def test_delete_unknown_edge(self):
        graph = self._sample_graph()
        patch = PipelineDiffPayload(base_revision=0, edges=EdgeDiff(delete=["nonexistent"]))
        result = apply_pipeline_patch(graph, patch)
        assert len(result["edges"]) == 2  # unchanged

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

    def test_patch_self_conflict_from_stale_inflight_revision(self, authed_client, pipeline, team_with_users):
        """Reproduce the false-conflict race from issue #3895.

        The debounced autosave in ``pipelineStore.ts`` reads ``base_revision`` from ``currentRevision``,
        which is only updated once a PATCH *response* lands (``pipelineStore.ts`` ``_patchPipeline`` success
        handler). There is no in-flight guard on the debounce, so when a user makes two quick successive
        edits, the second PATCH departs *before* the first response arrives and therefore carries the same,
        now-stale ``base_revision`` as the first.

        Server-side, the first PATCH has already incremented ``edit_revision``, so the second PATCH — even
        though it originates from the *same* session — fails the optimistic-concurrency check and returns a
        409, which the client surfaces as "This pipeline was modified in another session."

        This test emits that exact request sequence (two PATCHes with the same ``base_revision``) and asserts
        the second is rejected, demonstrating the self-conflict. A correct client would serialize its saves
        and send ``base_revision=1`` on the second PATCH.
        """
        team_slug = team_with_users.slug
        url = self._patch_url(team_slug, pipeline.id)

        def edit_payload(pos_y: int) -> dict:
            # Both edits are computed against the client's cached baseline, which still reports
            # base_revision=0 for the second edit because the first response has not been applied yet.
            return {
                "base_revision": 0,
                "nodes": {
                    "add": [],
                    "update": [
                        {
                            "id": "end",
                            "type": "endNode",
                            "position": {"x": 100, "y": pos_y},
                            "data": {"id": "end", "type": EndNode.__name__, "label": "End", "params": {}},
                        }
                    ],
                    "delete": [],
                },
                "edges": {"add": [], "update": [], "delete": []},
            }

        # Edit A → PATCH #1 succeeds and bumps the server revision to 1.
        response_1 = authed_client.patch(url, data=json.dumps(edit_payload(10)), content_type="application/json")
        assert response_1.status_code == 200
        assert response_1.json()["edit_revision"] == 1

        # Edit B → PATCH #2 fires while (in the real client) response #1 has not yet been applied,
        # so it reuses the stale base_revision=0 and is falsely rejected as a cross-session conflict.
        response_2 = authed_client.patch(url, data=json.dumps(edit_payload(20)), content_type="application/json")
        assert response_2.status_code == 409
        assert "conflict" in response_2.json()["error"].lower()
        # The server reports the true current revision the client should have used.
        assert response_2.json()["current_revision"] == 1

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
        end_node_id = None
        for node in pipeline.data.get("nodes", []):
            if node.get("data", {}).get("type") == EndNode.__name__:
                end_node_id = node["id"]
                break

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
        assert all(n.get("data", {}).get("type") != EndNode.__name__ for n in pipeline.data.get("nodes", []))

    def test_patch_updates_node_params(self, authed_client, pipeline, team_with_users):
        team_slug = team_with_users.slug
        llm_node_id = None
        for node in pipeline.data.get("nodes", []):
            if node.get("data", {}).get("type") == LLMResponseWithPrompt.__name__:
                llm_node_id = node["id"]
                break

        if not llm_node_id:
            pytest.skip("No LLM node in default pipeline")

        updated_node = {
            "id": llm_node_id,
            "type": "pipelineNode",
            "position": {"x": 100, "y": 0},
            "data": {
                "id": llm_node_id,
                "type": LLMResponseWithPrompt.__name__,
                "label": "Updated LLM",
                "params": {"prompt": "You are a helpful assistant."},
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
        updated_node_in_db = pipeline.node_set.get(flow_id=llm_node_id)
        assert "helpful assistant" in updated_node_in_db.params.get("prompt", "")


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

    def test_get_returns_edit_revision(self, authed_client, pipeline, team_with_users):
        team_slug = team_with_users.slug
        response = authed_client.get(
            self._post_url(team_slug, pipeline.id),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["pipeline"]["edit_revision"] == 0
