"""What the node model settles about a param's value, and what it only reports (#4140).

A loosely typed value is normalised on the way in; one that cannot be parsed is stored and reported
in ``pipeline_errors``. Only a reference the team cannot reach is refused; an undeclared param is
dropped.
"""

import pytest

from apps.pipelines.models import Node

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
def test_a_scalar_reference_sent_as_a_list_is_refused(client, chatbot, llm):
    """Nothing type-checks params before the reference check now, so it has to handle an unhashable
    value itself rather than raising on it."""
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
def test_an_unparseable_param_leaves_the_pipeline_readable(client, chatbot):
    client.post(nodes_url(chatbot), {"type": "CodeNode", "params": {"code": 123}}, format="json")

    inspect = client.get(f"/api/v2/chatbots/{chatbot.public_id}/inspect/")
    assert inspect.status_code == 200, inspect.content

    follow_up = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")
    assert follow_up.status_code == 201, follow_up.content


@pytest.mark.django_db()
def test_patch_stores_an_unparseable_param_and_reports_it(client, chatbot):
    created = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")
    node_id = created.json()["node"]["node_id"]

    response = client.patch(node_url(chatbot, node_id), {"params": {"code": 123}}, format="json")

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["pipeline_valid"] is False
    assert "TypeError" in body["pipeline_errors"]["node"][node_id]["root"]
    assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params["code"] == 123


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
