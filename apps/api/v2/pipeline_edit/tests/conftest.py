"""Fixtures for the pipeline façade tests (#4140).

A chatbot whose team can actually reference an LLM, because almost every node type needs one.
"""

from dataclasses import dataclass

import pytest

from apps.custom_actions.models import CustomAction
from apps.documents.models import Collection
from apps.experiments.models import SourceMaterial, SyntheticVoice
from apps.pipelines.models import Node
from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import ChatbotFactory, SourceMaterialFactory, SyntheticVoiceFactory
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    LlmProviderModelFactory,
    VoiceProviderFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def chatbot(team):
    """`ChatbotFactory` gives an Experiment with a Start -> End pipeline: the state a chatbot
    created on a team with no LLM provider is in, and the smallest graph to edit."""
    return ChatbotFactory.create(team=team, name="Support bot", description="")


@pytest.fixture()
def llm(team):
    """A provider and a model of the same type, which is what pairs them in `/pipeline/options/`."""
    provider = LlmProviderFactory.create(team=team, type="openai")
    model = LlmProviderModelFactory.create(team=team, type="openai", name="gpt-4o")
    return provider, model


@pytest.fixture()
def source_material(team):
    """`source_material_id`."""
    return SourceMaterialFactory.create(team=team, topic="Returns policy")


@pytest.fixture()
def media_collection(team):
    """`collection_id`. A media collection and an index are both `Collection` rows in one id space,
    split only by `is_index`, so the two fixtures differ in that alone."""
    return CollectionFactory.create(
        team=team, name="Policy docs", is_index=False, llm_provider=None, embedding_provider_model=None
    )


@pytest.fixture()
def collection_indexes(team):
    """`collection_index_ids`. Two of them, because selecting more than one is what obliges the
    prompt to use `{collection_index_summaries}`.

    Each carries a summary because that is what a local index is required to have once more than one
    is selected -- the summaries are what the model chooses between them with.
    """
    return [
        CollectionFactory.create(
            team=team,
            name=name,
            summary=summary,
            is_index=True,
            llm_provider=None,
            embedding_provider_model=None,
        )
        for name, summary in (("Support KB", "How-to articles"), ("Billing KB", "Invoices and refunds"))
    ]


@pytest.fixture()
def custom_action(team):
    """`custom_actions`, whose entries are the composite `"{action id}:{operation id}"` strings.

    `weather_get` is the one operation `CustomActionFactory`'s schema publishes *and* its
    `allowed_operations` permits, which is the pair the reference check insists on.
    """
    return CustomActionFactory.create(team=team, name="Orders API")


@pytest.fixture()
def synthetic_voice(team):
    """`synthetic_voice_id`. A voice is only reachable if the team holds a provider that can speak it
    -- matched on `SyntheticVoice.service` -- so the provider comes with it."""
    provider = VoiceProviderFactory.create(team=team, name="Prod Polly")
    return SyntheticVoiceFactory.create(name="Joanna", service="AWS", voice_provider=provider)


@dataclass
class LlmNodeResources:
    """Every resource an ``LLMResponseWithPrompt`` can reference, so a test naming them all takes one
    fixture rather than five."""

    source_material: SourceMaterial
    media_collection: Collection
    collection_indexes: list[Collection]
    custom_action: CustomAction
    synthetic_voice: SyntheticVoice


@pytest.fixture()
def llm_node_resources(source_material, media_collection, collection_indexes, custom_action, synthetic_voice):
    return LlmNodeResources(
        source_material=source_material,
        media_collection=media_collection,
        collection_indexes=collection_indexes,
        custom_action=custom_action,
        synthetic_voice=synthetic_voice,
    )


@pytest.fixture()
def client(chatbot):
    return ApiTestClient(chatbot.team.members.first(), chatbot.team)


@pytest.fixture()
def start(chatbot) -> str:
    return boundary_node(chatbot, "StartNode")


@pytest.fixture()
def end(chatbot) -> str:
    return boundary_node(chatbot, "EndNode")


def boundary_node(chatbot, node_type: str) -> str:
    """The flow id of the chatbot's Start or End node.

    A function as well as the two fixtures above, because a test that needs a *second* chatbot's
    boundary node cannot get it from a fixture bound to the first.
    """
    return chatbot.pipeline.node_set.get(type=node_type).flow_id


def nodes_url(chatbot) -> str:
    return f"/api/v2/chatbots/{chatbot.public_id}/pipeline/nodes/"


def node_url(chatbot, node_id: str) -> str:
    return f"/api/v2/chatbots/{chatbot.public_id}/pipeline/nodes/{node_id}/"


def edges_url(chatbot) -> str:
    return f"/api/v2/chatbots/{chatbot.public_id}/pipeline/edges/"


def edge_url(chatbot, edge_id: str) -> str:
    return f"/api/v2/chatbots/{chatbot.public_id}/pipeline/edges/{edge_id}/"


def stored_node_params(chatbot, node_id: str) -> dict:
    """The params on the node's row, which is the thing a later read serves."""
    return Node.objects.get(pipeline=chatbot.pipeline, flow_id=node_id).params


def add_edge(
    pipeline, source: str, target: str, source_handle: str | None = "output", target_handle: str | None = "input"
) -> str:
    """Wire two nodes by writing the edge straight into ``Pipeline.data``, as the UI builder's save
    does: ``Pipeline.data`` holds the edges and nothing else (ADR-0049).

    Still a direct write now that ``POST .../pipeline/edges/`` exists, because it can build edges
    that endpoint refuses -- one stranded on a handle its source does not offer, or one carrying the
    null ``targetHandle`` the UI builder writes -- and because a test about *nodes* should not fail
    when the edge endpoint changes.
    """
    edge_id = f"edge-{source}-{source_handle}-{target}"
    pipeline.data["edges"].append(
        {
            "id": edge_id,
            "source": source,
            "target": target,
            "sourceHandle": source_handle,
            "targetHandle": target_handle,
        }
    )
    pipeline.save(update_fields=["data"])
    return edge_id


def stored_edges(pipeline) -> list[dict]:
    """The edges the pipeline row actually holds, which is what a later read serves."""
    pipeline.refresh_from_db()
    return pipeline.data["edges"]


def wire(client, chatbot, source: str, target: str, **body) -> str:
    """Wire two nodes through the endpoint, and return the id the server assigned the edge."""
    response = client.post(edges_url(chatbot), {"source": source, "target": target, **body}, format="json")
    assert response.status_code == 201, response.content
    return response.json()["edge"]["id"]


def outgoing_handles(pipeline, source: str) -> dict[str, tuple[str, str]]:
    """``{edge_id: (sourceHandle, target)}`` for the stored edges leaving ``source``.

    Handle and target together, so one assertion says which edges survived an edit, which handle
    each ended up on, and that none changed where it points.
    """
    pipeline.refresh_from_db()
    return {
        edge["id"]: (edge["sourceHandle"], edge["target"])
        for edge in pipeline.data["edges"]
        if edge["source"] == source
    }


def add_llm_node(client, chatbot, llm) -> str:
    """An LLM node created the way an agent would, so its stored params are the full default set."""
    provider, model = llm
    response = client.post(
        nodes_url(chatbot),
        {
            "type": "LLMResponseWithPrompt",
            "params": {"llm_provider_id": provider.id, "llm_provider_model_id": model.id},
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    return response.json()["node"]["node_id"]


def add_bare_node(client, chatbot, node_type: str) -> str:
    """A node created from its type alone, so a PATCH of it has only defaults to overwrite."""
    response = client.post(nodes_url(chatbot), {"type": node_type}, format="json")
    assert response.status_code == 201, response.content
    return response.json()["node"]["node_id"]


def add_router_node(client, chatbot, llm, keywords=("schedule", "reschedule")) -> str:
    """A ``RouterNode``, whose output handles are its keywords, so a test has a source offering more
    than one. Not the only such type -- ``StaticRouterNode`` and the unpublished ``BooleanNode`` also
    offer several -- but the one an agent reaches for."""
    provider, model = llm
    response = client.post(
        nodes_url(chatbot),
        {
            "type": "RouterNode",
            "params": {
                "llm_provider_id": provider.id,
                "llm_provider_model_id": model.id,
                "keywords": list(keywords),
            },
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    return response.json()["node"]["node_id"]
