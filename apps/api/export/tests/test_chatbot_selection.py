"""What the export API serves for a team with three chatbots when two of them are selected.

Each chatbot has its own pipeline and nodes, providers, channel, and sessions with chats and
messages, plus a published version. Some rows are shared between a selected chatbot and the
unselected one. Every manifest resource is paged through the API exactly as the sync client does.
"""

from collections import defaultdict

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.urls import reverse

from apps.teams.export.chatbot_scope import CHATBOT_SCOPE_REGISTRY
from apps.teams.export.manifest import MANIFEST_ENTRIES
from apps.teams.export.translation import selection_key
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.factories.experiment import (
    ChatMessageFactory,
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantDataFactory,
    ParticipantFactory,
)
from apps.utils.factories.pipelines import NodeFactory
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    LlmProviderModelFactory,
    MessagingProviderFactory,
    VoiceProviderFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

pytestmark = pytest.mark.django_db

EXCLUDED_RESOURCES = [e.resource for e in MANIFEST_ENTRIES if CHATBOT_SCOPE_REGISTRY[e.model].build_q is None]


@pytest.fixture()
def team():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return TeamWithUsersFactory(public_key=public_pem.decode(), is_migrating=True)


def _admin(team):
    return next(m.user for m in team.membership_set.all() if m.is_team_admin())


def _chatbot(team, rows, shared_llm_provider=None):
    """A chatbot with its own rows, recorded in ``rows`` by resource name.

    Its pipeline gets an LLM node using its own provider and model, and a second one using
    ``shared_llm_provider`` when given. It gets a channel with a messaging provider, a session with
    two messages, participant data, and a published version.
    """
    voice_provider = VoiceProviderFactory(team=team)
    chatbot = ExperimentFactory(team=team, voice_provider=voice_provider, synthetic_voice=None)
    llm_provider = LlmProviderFactory(team=team)
    llm_provider_model = LlmProviderModelFactory(team=team)
    NodeFactory(pipeline=chatbot.pipeline, llm_provider=llm_provider, llm_provider_model=llm_provider_model)
    if shared_llm_provider is not None:
        NodeFactory(pipeline=chatbot.pipeline, llm_provider=shared_llm_provider)

    messaging_provider = MessagingProviderFactory(team=team)
    channel = ExperimentChannelFactory(team=team, experiment=chatbot, messaging_provider=messaging_provider)
    session = _session(chatbot, channel, participant=ParticipantFactory(team=team))
    participant_data = ParticipantDataFactory(team=team, experiment=chatbot, participant=session.participant)

    version = chatbot.create_new_version()

    rows["chatbots"] |= {chatbot.id, version.id}
    rows["pipelines"] |= {chatbot.pipeline_id, version.pipeline_id}
    rows["pipeline_nodes"] |= {
        *chatbot.pipeline.node_set.values_list("id", flat=True),
        *version.pipeline.node_set.values_list("id", flat=True),
    }
    rows["consent_forms"] |= {chatbot.consent_form_id, version.consent_form_id}
    rows["voice_providers"].add(voice_provider.id)
    rows["llm_providers"].add(llm_provider.id)
    rows["llm_provider_models"].add(llm_provider_model.id)
    rows["messaging_providers"].add(messaging_provider.id)
    rows["chatbot_channels"].add(channel.id)
    rows["participant_data"].add(participant_data.id)
    _record_session(rows, session)
    return chatbot, channel


def _session(chatbot, channel, participant):
    session = ExperimentSessionFactory(
        experiment=chatbot, team=chatbot.team, experiment_channel=channel, participant=participant
    )
    ChatMessageFactory(chat=session.chat, message_type="human", content="hi")
    ChatMessageFactory(chat=session.chat, message_type="ai", content="hello")
    return session


def _record_session(rows, session):
    rows["sessions"].add(session.id)
    rows["chats"].add(session.chat_id)
    rows["chat_messages"] |= set(session.chat.messages.values_list("id", flat=True))
    rows["participants"].add(session.participant_id)


@pytest.fixture()
def three_chatbots(team):
    """Chatbots A and B are selected; C is not.

    A and C share an LLM provider. One participant talked to both B and C, so their participant row
    is shared, while the session and chat with C belong to C alone.
    """
    a_rows, b_rows, c_rows, shared = (defaultdict(set) for _ in range(4))
    shared_llm_provider = LlmProviderFactory(team=team)
    shared["llm_providers"].add(shared_llm_provider.id)

    a, _a_channel = _chatbot(team, a_rows, shared_llm_provider=shared_llm_provider)
    b, b_channel = _chatbot(team, b_rows)
    c, c_channel = _chatbot(team, c_rows, shared_llm_provider=shared_llm_provider)

    both = ParticipantFactory(team=team)
    shared["participants"].add(both.id)
    b_session = _session(b, b_channel, participant=both)
    c_session = _session(c, c_channel, participant=both)
    _record_session(b_rows, b_session)
    _record_session(c_rows, c_session)
    b_rows["participants"].discard(both.id)
    c_rows["participants"].discard(both.id)

    EvaluatorFactory(team=team)
    team.exportable_experiments.add(a, b)
    return {"a": a, "b": b, "c": c, "rows": {"a": a_rows, "b": b_rows, "c": c_rows, "shared": shared}}


def _served(client, resource, selection=None, limit=2):
    """The ids of every row the resource serves, paged by cursor the way the sync client pages."""
    ids, cursor = [], None
    for _ in range(200):
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if selection:
            params["selection"] = selection
        response = client.get(reverse(f"api:export:resource-{resource}"), params)
        assert response.status_code == 200, (resource, response.content)
        page = response.json()
        ids += [row["id"] for row in page["results"]]
        cursor = page["cursor"]
        if not page["has_more"]:
            break
    else:
        pytest.fail(f"{resource} still had more rows after 200 pages")
    assert len(ids) == len(set(ids)), f"{resource} served a row twice"
    return set(ids)


TRACKED_RESOURCES = [
    "chatbots",
    "chatbot_channels",
    "pipelines",
    "pipeline_nodes",
    "consent_forms",
    "sessions",
    "chats",
    "chat_messages",
    "participants",
    "participant_data",
    "llm_providers",
    "llm_provider_models",
    "voice_providers",
    "messaging_providers",
]


def test_only_the_selected_chatbots_rows_are_served(team, three_chatbots):
    rows = three_chatbots["rows"]
    selection = selection_key([str(three_chatbots["a"].public_id), str(three_chatbots["b"].public_id)])
    client = ApiTestClient(_admin(team), team)

    mismatches = {}
    for resource in TRACKED_RESOURCES:
        served = _served(client, resource, selection=selection)
        expected = rows["a"][resource] | rows["b"][resource] | rows["shared"][resource]
        if served != expected:
            mismatches[resource] = {"missing": expected - served, "extra": served - expected}

    assert mismatches == {}


@pytest.mark.parametrize(
    "resource",
    [
        pytest.param("llm_providers", id="llm_provider-shared_by_a_and_c"),
        pytest.param("participants", id="participant-shared_by_b_and_c"),
    ],
)
def test_a_row_shared_with_the_unselected_chatbot_is_served(team, three_chatbots, resource):
    """C also uses the row, but a selected chatbot reaches it too."""
    client = ApiTestClient(_admin(team), team)
    selection = selection_key([str(three_chatbots["a"].public_id), str(three_chatbots["b"].public_id)])

    assert three_chatbots["rows"]["shared"][resource] <= _served(client, resource, selection=selection)


@pytest.mark.parametrize("resource", EXCLUDED_RESOURCES)
def test_an_excluded_resource_serves_nothing_under_the_selection(team, three_chatbots, resource):
    client = ApiTestClient(_admin(team), team)
    selection = selection_key([str(three_chatbots["a"].public_id), str(three_chatbots["b"].public_id)])

    assert _served(client, resource, selection=selection) == set()


def test_every_chatbots_rows_are_served_without_a_selection(team, three_chatbots):
    """C's rows are served once the selection is cleared, so their absence above comes from the selection.
    Team rows no chatbot uses, such as the team's default consent form, are served too."""
    team.exportable_experiments.clear()
    rows = three_chatbots["rows"]
    client = ApiTestClient(_admin(team), team)

    for resource in TRACKED_RESOURCES:
        expected = rows["a"][resource] | rows["b"][resource] | rows["c"][resource] | rows["shared"][resource]
        assert expected <= _served(client, resource, limit=1000), resource
    assert _served(client, "evaluators")
