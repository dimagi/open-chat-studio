"""What the node model settles about a param's value, and what it only reports (#4140).

A loosely typed value is normalised on the way in; one that cannot be parsed is stored and reported
in ``pipeline_errors``. Only a reference the team cannot reach — or one of a shape the check cannot
read — is refused.
"""

import pytest

from apps.pipelines.models import Node
from apps.utils.factories.documents import CollectionFactory

from .conftest import node_url, nodes_url


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("node_type", "params", "error_key"),
    [
        # `code`'s errors land under "root": its `mode="before"` validator raises `TypeError`,
        # which names no field.
        pytest.param("CodeNode", {"code": 123}, "root", id="int-for-string"),
        pytest.param("CodeNode", {"code": ["print(1)"]}, "root", id="array-for-string"),
        pytest.param("RouterNode", {"keywords": "one"}, "keywords", id="string-for-array"),
        pytest.param("RouterNode", {"keywords": [1, 2]}, "keywords", id="ints-for-string-array"),
        pytest.param("LLMResponseWithPrompt", {"max_history_length": "loads"}, "max_history_length", id="word-for-int"),
    ],
)
def test_a_param_of_an_unparseable_type_persists_and_is_reported(client, chatbot, node_type, params, error_key):
    response = client.post(nodes_url(chatbot), {"type": node_type, "params": params}, format="json")

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["pipeline_valid"] is False
    assert error_key in body["pipeline_errors"]["node"][body["node"]["node_id"]]
    assert Node.objects.filter(pipeline=chatbot.pipeline, type=node_type).exists()


@pytest.mark.django_db()
def test_patch_stores_an_unparseable_param_and_reports_it(client, chatbot):
    created = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")
    node_id = created.json()["node"]["node_id"]

    response = client.patch(node_url(chatbot, node_id), {"params": {"code": 123}}, format="json")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["pipeline_valid"] is False
    assert body["pipeline_errors"]["node"][node_id]["root"]
    assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params["code"] == 123


@pytest.mark.django_db()
def test_a_list_sent_for_a_scalar_reference_is_refused(client, chatbot, llm):
    """Nothing type-checks params before the reference check, so it has to handle a value of the
    wrong shape itself rather than raising on it. Here that means an unhashable one."""
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


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "tools",
    [
        pytest.param([{"a": 1}], id="dict-in-the-list"),
        pytest.param({"a": 1}, id="dict-for-the-list"),
        pytest.param([["one-off-reminder"]], id="a-real-name-nested-in-a-list"),
        pytest.param(["not_a_tool"], id="unknown-name"),
    ],
)
def test_a_tool_the_team_cannot_use_is_refused(client, chatbot, tools):
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


@pytest.mark.django_db()
def test_a_scalar_sent_for_a_list_valued_reference_is_refused(client, chatbot, team):
    """The other shape: a bare id where a list belongs. Read as one unreachable value rather than
    iterated over as if it were a list."""
    ours = CollectionFactory.create(team=team, is_index=True)

    response = client.post(
        nodes_url(chatbot),
        {"type": "LLMResponseWithPrompt", "params": {"collection_index_ids": ours.id + 1000}},
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "collection_index_ids" in response.json()["params"]


@pytest.mark.django_db()
def test_a_loosely_typed_value_is_normalised_on_the_way_in(client, chatbot, llm):
    provider, model = llm
    created = client.post(
        nodes_url(chatbot),
        {
            "type": "LLMResponseWithPrompt",
            "params": {"llm_provider_id": provider.id, "llm_provider_model_id": model.id},
        },
        format="json",
    )
    node_id = created.json()["node"]["node_id"]

    response = client.patch(node_url(chatbot, node_id), {"params": {"max_history_length": "12"}}, format="json")

    assert response.status_code == 200, response.content
    assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params["max_history_length"] == 12


@pytest.mark.django_db()
def test_clearing_a_reference_with_null_is_still_allowed(client, chatbot, llm):
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
