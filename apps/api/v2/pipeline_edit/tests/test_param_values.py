"""What a write settles about a param's value, and what it only reports (#4140).

Two rules, and the split between them is the point of this file. A loosely typed value is normalised
on the way in, and one that cannot be parsed at all is stored and reported in ``pipeline_errors`` --
the node still lands, so an agent can build a graph a piece at a time. What is *refused* is a
reference: an id naming a resource the team cannot reach, or a value of a shape the check cannot
read. Nothing downstream would tell the caller about one of those (``references.py``), so the write
does.

The check is the same for POST and PATCH, so the reference classes below cover both verbs rather
than living beside one of them.
"""

import pytest

from apps.pipelines.models import Node
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import SourceMaterialFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory

from .conftest import add_llm_node, node_url, nodes_url


@pytest.mark.django_db()
class TestParsedAndNormalised:
    """A value the model can make sense of is stored as it reads it; one it cannot is stored as sent
    and reported."""

    @pytest.mark.parametrize(
        ("node_type", "params", "error_key"),
        [
            # `code`'s errors land under "root": its `mode="before"` validator raises `TypeError`,
            # which names no field.
            pytest.param("CodeNode", {"code": 123}, "root", id="int-for-string"),
            pytest.param("CodeNode", {"code": ["print(1)"]}, "root", id="array-for-string"),
            pytest.param("RouterNode", {"keywords": "one"}, "keywords", id="string-for-array"),
            pytest.param("RouterNode", {"keywords": [1, 2]}, "keywords", id="ints-for-string-array"),
            pytest.param(
                "LLMResponseWithPrompt", {"max_history_length": "loads"}, "max_history_length", id="word-for-int"
            ),
        ],
    )
    def test_a_param_of_an_unparseable_type_persists_and_is_reported(
        self, client, chatbot, node_type, params, error_key
    ):
        response = client.post(nodes_url(chatbot), {"type": node_type, "params": params}, format="json")

        assert response.status_code == 201, response.content
        body = response.json()
        assert body["pipeline_valid"] is False
        assert error_key in body["pipeline_errors"]["node"][body["node"]["node_id"]]
        assert Node.objects.filter(pipeline=chatbot.pipeline, type=node_type).exists()

    def test_patch_stores_an_unparseable_param_and_reports_it(self, client, chatbot):
        created = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")
        node_id = created.json()["node"]["node_id"]

        response = client.patch(node_url(chatbot, node_id), {"params": {"code": 123}}, format="json")

        assert response.status_code == 200, response.content
        body = response.json()
        assert body["pipeline_valid"] is False
        assert body["pipeline_errors"]["node"][node_id]["root"]
        assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params["code"] == 123

    def test_a_loosely_typed_value_is_normalised_on_the_way_in(self, client, chatbot, llm):
        node_id = add_llm_node(client, chatbot, llm)

        response = client.patch(node_url(chatbot, node_id), {"params": {"max_history_length": "12"}}, format="json")

        assert response.status_code == 200, response.content
        assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params["max_history_length"] == 12


@pytest.mark.django_db()
class TestReferencesMustBeReachable:
    """An id is taken only if it names something the team holds. Refused rather than reported: the id
    would otherwise land in the node's FK column untouched and ``Pipeline.validate`` would say
    nothing about it."""

    def test_a_reference_to_another_teams_resource_is_refused(self, client, chatbot):
        """Indistinguishable from a nonexistent id on purpose: telling the two apart would answer
        whether the id exists in some other team."""
        elsewhere = LlmProviderFactory.create(team=TeamWithUsersFactory.create(), type="openai")

        response = client.post(
            nodes_url(chatbot),
            {"type": "LLMResponseWithPrompt", "params": {"llm_provider_id": elsewhere.id}},
            format="json",
        )

        assert response.status_code == 400, response.content
        assert "llm_provider_id" in response.json()["params"]
        assert not chatbot.pipeline.node_set.filter(type="LLMResponseWithPrompt").exists()

    def test_a_reference_to_the_teams_own_resource_is_accepted(self, client, chatbot, team):
        """Guards the guard: the refusal above has to be about the team boundary, not about the
        reference check refusing every id it is shown."""
        material = SourceMaterialFactory.create(team=team)

        response = client.post(
            nodes_url(chatbot),
            {"type": "LLMResponseWithPrompt", "params": {"source_material_id": material.id}},
            format="json",
        )

        assert response.status_code == 201, response.content

    def test_patch_refuses_another_teams_resource(self, client, chatbot, llm):
        node_id = add_llm_node(client, chatbot, llm)
        elsewhere = LlmProviderFactory.create(team=TeamWithUsersFactory.create(), type="openai")

        response = client.patch(
            node_url(chatbot, node_id), {"params": {"llm_provider_id": elsewhere.id}}, format="json"
        )

        assert response.status_code == 400, response.content
        assert "llm_provider_id" in response.json()["params"]

    def test_patch_accepts_the_teams_own_resource(self, client, chatbot, llm):
        """Guards the guard on the PATCH side: the option lists are built in the view and handed to
        ``plan_update``, so a PATCH that names a reference has to actually get them -- handing over an
        empty set would refuse every id, which the refusal above cannot tell apart from working."""
        node_id = add_llm_node(client, chatbot, llm)
        ours = LlmProviderFactory.create(team=chatbot.team, type="openai")

        response = client.patch(node_url(chatbot, node_id), {"params": {"llm_provider_id": ours.id}}, format="json")

        assert response.status_code == 200, response.content
        assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params["llm_provider_id"] == ours.id

    def test_a_list_valued_reference_is_checked_per_entry(self, client, chatbot, team):
        """`collection_index_ids` holds a list, so the check has to look at every entry rather than at
        the list as a whole."""
        ours = CollectionFactory.create(team=team, is_index=True)
        theirs = CollectionFactory.create(team=TeamWithUsersFactory.create(), is_index=True)

        response = client.post(
            nodes_url(chatbot),
            {"type": "LLMResponseWithPrompt", "params": {"collection_index_ids": [ours.id, theirs.id]}},
            format="json",
        )

        assert response.status_code == 400, response.content
        assert str(theirs.id) in response.json()["params"]["collection_index_ids"]
        assert str(ours.id) not in response.json()["params"]["collection_index_ids"]

    def test_clearing_a_reference_with_null_is_still_allowed(self, client, chatbot, llm):
        provider, _model = llm
        created = client.post(
            nodes_url(chatbot),
            {"type": "LLMResponseWithPrompt", "params": {"llm_provider_id": provider.id}},
            format="json",
        )
        node_id = created.json()["node"]["node_id"]

        response = client.patch(node_url(chatbot, node_id), {"params": {"llm_provider_id": None}}, format="json")

        assert response.status_code == 200, response.content
        assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params["llm_provider_id"] is None


@pytest.mark.django_db()
class TestReferencesOfTheWrongShape:
    """Nothing type-checks params before the reference check, so it has to answer for a value of a
    shape it cannot read rather than raise on it -- each of these would be a 500 in place of a 400.
    """

    def test_a_list_sent_for_a_scalar_reference_is_refused(self, client, chatbot, llm):
        """An unhashable value where one id belongs."""
        provider, model = llm

        response = client.post(
            nodes_url(chatbot),
            {
                "type": "LLMResponseWithPrompt",
                "params": {"llm_provider_id": provider.id, "llm_provider_model_id": [model.id]},
            },
            format="json",
        )

        assert response.status_code == 400, response.content
        assert "llm_provider_model_id" in response.json()["params"]
        assert not Node.objects.filter(pipeline=chatbot.pipeline, type="LLMResponseWithPrompt").exists()

    def test_a_scalar_sent_for_a_list_valued_reference_is_refused(self, client, chatbot, team):
        """The other way round: a bare id where a list belongs. Read as one unreachable value rather
        than iterated over as if it were a list."""
        ours = CollectionFactory.create(team=team, is_index=True)

        response = client.post(
            nodes_url(chatbot),
            {"type": "LLMResponseWithPrompt", "params": {"collection_index_ids": ours.id + 1000}},
            format="json",
        )

        assert response.status_code == 400, response.content
        assert "collection_index_ids" in response.json()["params"]

    @pytest.mark.parametrize(
        "tools",
        [
            pytest.param([{"a": 1}], id="dict-in-the-list"),
            pytest.param({"a": 1}, id="dict-for-the-list"),
            pytest.param([["one-off-reminder"]], id="a-real-name-nested-in-a-list"),
            pytest.param(["not_a_tool"], id="unknown-name"),
        ],
    )
    def test_a_tool_the_team_cannot_use_is_refused(self, client, chatbot, tools):
        """``tools`` is the one reference whose values are names rather than ids, so it is the one
        resolver that never parses what it was handed. It still has to answer rather than raise: an
        unhashable value cannot be looked up, and asking would be a 500 in place of this 400.
        """
        response = client.post(
            nodes_url(chatbot), {"type": "LLMResponseWithPrompt", "params": {"tools": tools}}, format="json"
        )

        assert response.status_code == 400, response.content
        assert "tools" in response.json()["params"]
        assert not Node.objects.filter(pipeline=chatbot.pipeline, type="LLMResponseWithPrompt").exists()

    def test_a_malformed_custom_action_reference_is_refused(self, client, chatbot):
        """`custom_actions` entries are the composite "{action_id}:{operation_id}" strings the server
        hands out, and `Node.update_from_params` splits them on the colon -- so a value that is not
        one would be a 500 rather than a rejected write if it reached the save."""
        response = client.post(
            nodes_url(chatbot),
            {"type": "LLMResponseWithPrompt", "params": {"custom_actions": ["not-a-reference"]}},
            format="json",
        )

        assert response.status_code == 400, response.content
        assert "custom_actions" in response.json()["params"]
        assert not chatbot.pipeline.node_set.filter(type="LLMResponseWithPrompt").exists()
