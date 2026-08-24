"""Fixtures for the pipeline façade tests (#4140).

A chatbot whose team can actually reference an LLM, because almost every node type needs one.
"""

import pytest

from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory
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
def client(chatbot):
    return ApiTestClient(chatbot.team.members.first(), chatbot.team)


def nodes_url(chatbot) -> str:
    return f"/api/v2/chatbots/{chatbot.public_id}/pipeline/nodes/"


def node_url(chatbot, node_id: str) -> str:
    return f"/api/v2/chatbots/{chatbot.public_id}/pipeline/nodes/{node_id}/"


def add_edge(pipeline, source: str, target: str, source_handle: str = "output") -> str:
    """Wire two nodes by writing the edge straight into ``Pipeline.data``.

    The edge endpoints are a separate ticket, so the tests here that need a wired graph build one
    the way the builder's save does: ``Pipeline.data`` holds the edges and nothing else (ADR-0049).
    """
    edge_id = f"edge-{source}-{source_handle}-{target}"
    pipeline.data["edges"].append(
        {
            "id": edge_id,
            "source": source,
            "target": target,
            "sourceHandle": source_handle,
            "targetHandle": "input",
        }
    )
    pipeline.save(update_fields=["data"])
    return edge_id


def outgoing_handles(pipeline, source: str) -> dict[str, tuple[str, str]]:
    """``{edge_id: (sourceHandle, target)}`` for the stored edges leaving ``source``.

    Handle and target together, so one assertion says which edges survived an edit, which handle
    each ended up on, and that none of them changed where it points.
    """
    pipeline.refresh_from_db()
    return {
        edge["id"]: (edge["sourceHandle"], edge["target"])
        for edge in pipeline.data["edges"]
        if edge["source"] == source
    }
