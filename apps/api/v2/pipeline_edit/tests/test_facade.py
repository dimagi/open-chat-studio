"""The write cycle every façade endpoint shares (#4140, W2/W7)."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.pipelines.models import Node

from .conftest import node_url, nodes_url


@pytest.mark.django_db()
def test_a_write_locks_the_pipeline_row(client, chatbot):
    """The graph is read, merged and written back, so without the lock two concurrent writes would
    each merge into the pre-write graph and the second would drop the first. Query capture rather
    than threads: a real concurrency test would be slow and flaky."""
    with CaptureQueriesContext(connection) as captured:
        assert client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json").status_code == 201

    locked = [query for query in captured.captured_queries if "FOR UPDATE" in query["sql"]]
    assert any('"pipelines_pipeline"' in query["sql"] for query in locked), locked


@pytest.mark.django_db()
@pytest.mark.parametrize("verb", ["post", "patch"])
def test_the_option_lists_are_built_before_the_row_is_locked(client, chatbot, llm, verb):
    """Checking a reference costs around fifteen queries and parses every custom action's OpenAPI
    schema. Doing that while holding the pipeline row would serialise concurrent edits to the same
    chatbot for the whole of it, so it happens before the lock is taken."""
    provider, model = llm
    params = {"llm_provider_id": provider.id, "llm_provider_model_id": model.id}
    node_id = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json").json()["node"][
        "node_id"
    ]

    with CaptureQueriesContext(connection) as captured:
        if verb == "post":
            client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "params": params}, format="json")
        else:
            client.patch(node_url(chatbot, node_id), {"params": params}, format="json")

    sql = [query["sql"] for query in captured.captured_queries]
    lock = next(index for index, query in enumerate(sql) if "FOR UPDATE" in query and '"pipelines_pipeline"' in query)
    # The team-scoped provider list, which only `options_for_team` asks for -- as opposed to the
    # by-id existence checks `_sync_resource_fk_fields` runs while persisting, which belong inside.
    built = [index for index, query in enumerate(sql) if '"service_providers_llmprovider"."team_id"' in query]

    assert built, "the option lists were never built, so this proves nothing"
    assert max(built) < lock, f"option lists were built under the lock (at {built}, lock at {lock})"


@pytest.mark.django_db()
def test_a_write_bumps_the_edit_revision(client, chatbot):
    """The builder refuses a save whose `base_revision` has moved on. An API write that left the
    revision alone would let an open builder session overwrite it without ever seeing a conflict."""
    before = chatbot.pipeline.edit_revision

    client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    chatbot.pipeline.refresh_from_db()
    assert chatbot.pipeline.edit_revision == before + 1


@pytest.mark.django_db()
def test_a_refused_write_changes_nothing(client, chatbot):
    """The reference check runs inside the transaction, so a refusal has to leave the revision
    where it was as well as the graph."""
    before = chatbot.pipeline.edit_revision

    response = client.post(
        nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "params": {"llm_provider_id": 9999}}, format="json"
    )

    assert response.status_code == 400, response.content
    chatbot.pipeline.refresh_from_db()
    assert chatbot.pipeline.edit_revision == before


@pytest.mark.django_db()
def test_a_created_node_reads_back_the_way_it_was_returned(client, chatbot, llm):
    """The payoff of writing the type's defaults rather than only reporting them: inspect serves
    stored params verbatim, so anything not persisted would simply be missing on the next read."""
    provider, model = llm
    created = client.post(
        nodes_url(chatbot),
        {
            "type": "LLMResponseWithPrompt",
            "params": {"llm_provider_id": provider.id, "llm_provider_model_id": model.id},
        },
        format="json",
    ).json()["node"]

    stored = Node.objects.get(pipeline=chatbot.pipeline, flow_id=created["node_id"]).params
    # Every param the write reported is on the row with the value it reported -- and so are the
    # defaults for the params the API withholds from its schema, which the write response leaves
    # out only because PATCH would refuse them back.
    assert created["params"].items() <= stored.items()
    assert "mcp_tools" in stored
    assert "mcp_tools" not in created["params"]

    inspected = client.get(f"/api/v2/chatbots/{chatbot.public_id}/inspect/").json()
    node = next(node for node in inspected["pipeline"]["nodes"] if node["node_id"] == created["node_id"])

    # Inspect keeps resource ids and `name` out of params, renders the resources separately, and
    # renames one param; everything else it reports is what was stored.
    reported = {"max_indexed_collection_search_results": "max_results"}
    assert {reported.get(key, key): value for key, value in node["params"].items()}.items() <= stored.items()
    assert node["params"]["history_type"] == "global"
    assert node["llm"]["provider_id"] == provider.id
    assert node["output_handles"] == created["output_handles"]


@pytest.mark.django_db()
def test_a_write_reports_the_state_it_produced(client, chatbot, llm):
    """The envelope is built inside the transaction, so a body that cannot be built takes the write
    down with it rather than leaving a node behind that no later call can read or address."""
    provider, model = llm
    body = client.post(
        nodes_url(chatbot),
        {
            "type": "LLMResponseWithPrompt",
            "params": {"llm_provider_id": provider.id, "llm_provider_model_id": model.id},
        },
        format="json",
    ).json()

    node_id = body["node"]["node_id"]
    assert body["pipeline_valid"] is True
    assert body["pipeline_errors"] == {"node": {}, "edge": [], "pipeline": []}
    assert body["unwired_handles"][node_id] == [
        {"handle": "input", "label": None},
        {"handle": "output", "label": None},
    ]


@pytest.mark.django_db()
def test_a_write_to_a_chatbot_with_no_pipeline_is_a_404(client, chatbot):
    """Every chatbot the UI or POST /chatbots/ makes is pipeline-backed, but nothing in the schema
    enforces it, so an older row without one is 'nothing to edit' rather than a 500."""
    chatbot.pipeline = None
    chatbot.save(update_fields=["pipeline"])

    response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

    assert response.status_code == 404, response.content


@pytest.mark.django_db()
def test_a_version_snapshot_cannot_be_edited(client, chatbot, llm):
    """Snapshots are immutable: writes only ever target the working version."""
    version = chatbot.create_new_version()

    response = client.post(nodes_url(version), {"type": "LLMResponseWithPrompt"}, format="json")

    assert response.status_code == 404, response.content


@pytest.mark.django_db()
def test_deleting_a_node_leaves_the_other_rows_alone(client, chatbot, llm):
    """`update_nodes_from_data` reads its mapping as the whole graph membership, so a diff that
    named only the deleted node would reconcile every other node away."""
    provider, model = llm
    node_id = client.post(
        nodes_url(chatbot),
        {
            "type": "LLMResponseWithPrompt",
            "params": {"llm_provider_id": provider.id, "llm_provider_model_id": model.id},
        },
        format="json",
    ).json()["node"]["node_id"]

    client.delete(node_url(chatbot, node_id))

    assert set(chatbot.pipeline.node_set.values_list("type", flat=True)) == {"StartNode", "EndNode"}
