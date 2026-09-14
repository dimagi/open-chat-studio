"""What the façade does about a deprecated LLM model: refuses it as a new value, reports an
existing one as advisory.

The two halves pull in opposite directions on purpose. A deprecated model is on its way out, so a
client may not newly point a node at one; but the teams already on one are inside a migration
window, and a write that refused to touch such a node would trap them in it.
"""

import pytest

from apps.utils.factories.service_provider_factories import LlmProviderModelFactory

from .conftest import add_llm_node, node_url, nodes_url


@pytest.fixture()
def deprecated_model(team):
    return LlmProviderModelFactory.create(team=team, type="openai", name="gpt-3.5-turbo", deprecated=True)


@pytest.mark.django_db()
def test_a_new_node_cannot_be_pointed_at_a_deprecated_model(client, chatbot, llm, deprecated_model):
    provider, _ = llm

    response = client.post(
        nodes_url(chatbot),
        {
            "type": "LLMResponseWithPrompt",
            "params": {"llm_provider_id": provider.id, "llm_provider_model_id": deprecated_model.id},
        },
        format="json",
    )

    assert response.status_code == 400, response.content
    assert "llm_provider_model_id" in response.json()["params"]


@pytest.mark.django_db()
def test_an_existing_node_cannot_be_moved_onto_a_deprecated_model(client, chatbot, llm, deprecated_model):
    node_id = add_llm_node(client, chatbot, llm)

    response = client.patch(
        node_url(chatbot, node_id), {"params": {"llm_provider_model_id": deprecated_model.id}}, format="json"
    )

    assert response.status_code == 400, response.content
    assert "llm_provider_model_id" in response.json()["params"]


@pytest.mark.django_db()
def test_a_node_already_on_a_deprecated_model_stays_writable(client, chatbot, llm, deprecated_model):
    """The migration window in practice: the model is deprecated under a team that already uses it,
    and every other edit to that node has to keep working -- including the edit that moves it off."""
    provider, live_model = llm
    node_id = add_llm_node(client, chatbot, llm)
    client.patch(node_url(chatbot, node_id), {"params": {"llm_provider_model_id": live_model.id}}, format="json")
    replacement = LlmProviderModelFactory.create(team=chatbot.team, type="openai", name="gpt-4.1-mini")
    live_model.deprecated = True
    live_model.save()

    renamed = client.patch(node_url(chatbot, node_id), {"label": "Answer the question"}, format="json")

    assert renamed.status_code == 200, renamed.content
    body = renamed.json()
    assert body["pipeline_valid"] is True
    assert body["pipeline_errors"] == {"node": {}, "edge": [], "pipeline": []}
    assert body["deprecated_models"] == {node_id: {"model": "gpt-4o", "replacement": None}}

    moved = client.patch(
        node_url(chatbot, node_id),
        {"params": {"llm_provider_id": provider.id, "llm_provider_model_id": replacement.id}},
        format="json",
    )

    assert moved.status_code == 200, moved.content
    assert moved.json()["deprecated_models"] == {}
