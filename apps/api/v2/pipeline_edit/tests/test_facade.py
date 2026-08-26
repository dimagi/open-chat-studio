"""What every façade endpoint shares: the routes, and the write cycle behind them (#4140, W2/W7)."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.pipelines.models import Node
from apps.utils.factories.documents import CollectionFactory

from .conftest import node_url, nodes_url


@pytest.mark.django_db()
class TestRoutes:
    """One view class serves the collection and the detail route, so what each offers is resolved
    per route rather than per class."""

    @pytest.mark.parametrize(
        ("route", "verb", "fields"),
        [
            pytest.param("collection", "POST", {"type", "label", "params"}, id="collection-post"),
            pytest.param("detail", "PATCH", {"label", "params"}, id="detail-patch"),
        ],
    )
    def test_options_describes_the_body_the_route_takes(self, client, chatbot, route, verb, fields):
        """The body described has to be resolved per verb rather than per class -- and since PATCH is
        the detail route's only writable verb, stock DRF metadata (PUT and POST alone) would answer it
        with no body at all."""
        node_id = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json").json()["node"]["node_id"]
        url = nodes_url(chatbot) if route == "collection" else node_url(chatbot, node_id)

        response = client.options(url)

        assert response.status_code == 200, response.content
        assert set(response.json()["actions"][verb]) == fields

    def test_a_verb_the_route_does_not_offer_is_a_405(self, client, chatbot):
        """Each `path()` narrows `http_method_names` to the verbs its own route offers -- without
        that, a verb from the other route reaches its handler with the wrong path kwargs and raises
        a 500."""
        node_id = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json").json()["node"]["node_id"]

        assert client.patch(nodes_url(chatbot), {"params": {}}, format="json").status_code == 405
        assert client.delete(nodes_url(chatbot)).status_code == 405
        assert client.post(node_url(chatbot, node_id), {"type": "CodeNode"}, format="json").status_code == 405


@pytest.mark.django_db()
class TestWhichPipelineAWriteTargets:
    """Only a chatbot's live, working pipeline can be edited. Each of these is a 404 rather than the
    500 that reaching for a row that is not there would otherwise be."""

    def test_a_write_to_a_chatbot_with_no_pipeline_is_a_404(self, client, chatbot):
        """Every chatbot the UI or POST /chatbots/ makes is pipeline-backed, but nothing in the
        schema enforces it, so an older row without one is "nothing to edit"."""
        chatbot.pipeline = None
        chatbot.save(update_fields=["pipeline"])

        response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

        assert response.status_code == 404, response.content

    def test_a_write_to_an_archived_pipeline_is_a_404(self, client, chatbot):
        """The default manager hides archived rows, so reaching for one by pk is a ``DoesNotExist``
        rather than a miss."""
        chatbot.pipeline.is_archived = True
        chatbot.pipeline.save(update_fields=["is_archived"])

        response = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

        assert response.status_code == 404, response.content

    def test_a_version_snapshot_cannot_be_edited(self, client, chatbot):
        """Snapshots are immutable: writes only ever target the working version."""
        version = chatbot.create_new_version()

        response = client.post(nodes_url(version), {"type": "LLMResponseWithPrompt"}, format="json")

        assert response.status_code == 404, response.content


@pytest.mark.django_db()
class TestTheWriteCycle:
    """A write holds the pipeline row, applies its diff and bumps the revision, all or nothing."""

    def test_a_write_bumps_the_edit_revision(self, client, chatbot):
        """The UI builder refuses a save whose `base_revision` has moved on, so an API write that
        left the revision alone would let an open builder session overwrite it with no conflict
        shown."""
        before = chatbot.pipeline.edit_revision

        client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json")

        chatbot.pipeline.refresh_from_db()
        assert chatbot.pipeline.edit_revision == before + 1

    def test_a_refused_write_changes_nothing(self, client, chatbot):
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
class TestTheResponseEnvelope:
    """Every write answers with the state it produced, built inside the transaction that produced
    it."""

    def test_a_write_reports_the_state_it_produced(self, client, chatbot, llm):
        """A body that cannot be built takes the write down with it rather than leaving a node behind
        that no later call can read or address."""
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

    def test_a_created_node_reads_back_the_way_it_was_returned(self, client, chatbot, llm):
        """Why the type's defaults are written rather than only reported: inspect serves stored params
        verbatim, so anything not persisted would simply be missing on the next read."""
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
        # Every param the write reported is on the row with the value it reported, as are the defaults
        # for the withheld params -- left out of the response only because PATCH would refuse them back.
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


#: How many queries a PATCH may run while it holds the pipeline row. Pinned to a number rather than
#: compared against itself because rebuilding the team's option lists in there -- how the check used
#: to work -- adds a dozen at a stroke and nothing else would notice. Raising it is a decision about
#: how long the row is held, so say in the commit what the extra queries buy.
QUERIES_UNDER_THE_LOCK = 27


@pytest.mark.django_db()
def test_a_reference_check_reads_only_the_ids_it_was_sent(client, chatbot, llm, team):
    """A PATCH only learns the node's type from the locked graph, so its reference check runs inside
    the lock. What keeps that from serialising concurrent edits is that the check asks after the ids
    it was sent rather than building the lists to pick them out of.

    Two things have to hold, and they fail on different axes:

    * the count stays at :data:`QUERIES_UNDER_THE_LOCK` -- building the option lists in here is a
      fixed dozen extra queries, which only an absolute number catches.
    * it does not move with the number of ids sent, the axis a resolver querying per value instead
      of per param would grow on.

    Query counting rather than threads: a real concurrency test would be slow and flaky.
    """
    provider, model = llm
    node_id = client.post(nodes_url(chatbot), {"type": "LLMResponseWithPrompt"}, format="json").json()["node"][
        "node_id"
    ]

    def patch_cost(params: dict) -> int:
        with CaptureQueriesContext(connection) as captured:
            response = client.patch(node_url(chatbot, node_id), {"params": params}, format="json")
        assert response.status_code == 200, response.content
        sql = [query["sql"] for query in captured.captured_queries]
        locks = [index for index, query in enumerate(sql) if "FOR UPDATE" in query and '"pipelines_pipeline"' in query]
        assert locks, "the pipeline row was never locked"
        return len(sql) - min(locks)

    llm_params = {"llm_provider_id": provider.id, "llm_provider_model_id": model.id}
    patch_cost(llm_params)  # discarded: the first write of the process warms caches the later ones reuse
    assert patch_cost(llm_params) == QUERIES_UNDER_THE_LOCK, "see the constant"

    indexes = [
        CollectionFactory.create(
            team=team,
            name=f"KB {index}",
            summary=f"KB {index}",
            is_index=True,
            llm_provider=None,
            embedding_provider_model=None,
        )
        for index in range(12)
    ]
    one_id = patch_cost({"collection_index_ids": [indexes[0].id]})

    assert patch_cost({"collection_index_ids": [index.id for index in indexes]}) == one_id
