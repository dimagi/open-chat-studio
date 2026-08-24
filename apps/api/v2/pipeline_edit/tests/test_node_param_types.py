"""A param whose *value* is the wrong shape is refused, not persisted (#4140).

Lenient persistence is about a graph that is incomplete, not about one that cannot be read back.
A param the node type cannot even parse is refused up front, because storing it breaks
``Pipeline.validate()`` for every later read and write of that pipeline — the node has no id the
caller could delete it by, so nothing short of the builder could repair it.
"""

import pytest

from apps.pipelines.models import Node

from .conftest import node_url, nodes_url


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("node_type", "params"),
    [
        pytest.param("CodeNode", {"code": 123}, id="int-for-string"),
        pytest.param("LLMResponseWithPrompt", {"llm_provider_id": "3"}, id="string-for-int"),
        pytest.param("RouterNode", {"keywords": "one"}, id="string-for-array"),
        pytest.param("RouterNode", {"keywords": [1, 2]}, id="ints-for-string-array"),
        pytest.param("CodeNode", {"code": ["print(1)"]}, id="array-for-string"),
    ],
)
def test_a_param_of_an_unparseable_type_is_refused(client, chatbot, node_type, params):
    response = client.post(nodes_url(chatbot), {"type": node_type, "params": params}, format="json")

    assert response.status_code == 400, response.content
    assert set(response.json()["params"]) == set(params)
    assert not Node.objects.filter(pipeline=chatbot.pipeline, type=node_type).exists()


@pytest.mark.django_db()
def test_a_scalar_reference_sent_as_a_list_is_refused(client, chatbot, llm):
    """The ordinary agent mistake: wrapping a scalar id in a one-element array.

    It has to be caught by the type check rather than by the reference check, which used to flatten
    any value to a list and so read ``[id]`` as a list-valued param holding a valid id.
    """
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
def test_a_refused_param_leaves_the_pipeline_readable(client, chatbot):
    """The point of refusing rather than reporting: everything else still works afterwards."""
    client.post(nodes_url(chatbot), {"type": "CodeNode", "params": {"code": 123}}, format="json")

    inspect = client.get(f"/api/v2/chatbots/{chatbot.public_id}/inspect/")
    assert inspect.status_code == 200, inspect.content

    follow_up = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")
    assert follow_up.status_code == 201, follow_up.content


@pytest.mark.django_db()
def test_patch_refuses_an_unparseable_param_and_leaves_the_node_alone(client, chatbot):
    created = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")
    node_id = created.json()["node"]["node_id"]
    before = Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params

    response = client.patch(node_url(chatbot, node_id), {"params": {"code": 123}}, format="json")

    assert response.status_code == 400, response.content
    assert Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params == before


@pytest.mark.django_db()
def test_clearing_a_reference_with_null_is_still_allowed(client, chatbot, llm):
    """``None`` means "unset", so the type check must not read it as a wrong-typed integer."""
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
def test_a_semantically_wrong_but_parseable_param_still_persists(client, chatbot, llm):
    """Guards the guard: the type check must not swallow the lenient case it sits next to.

    ``max_history_length`` is declared an integer and sent one; that it is nonsensical is the
    node's business to report, not the serializer's to refuse.
    """
    provider, model = llm

    response = client.post(
        nodes_url(chatbot),
        {
            "type": "LLMResponseWithPrompt",
            "params": {"llm_provider_id": provider.id, "llm_provider_model_id": model.id, "max_history_length": -5},
        },
        format="json",
    )

    assert response.status_code == 201, response.content
