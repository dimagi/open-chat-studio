import json
from types import SimpleNamespace

import pytest
from django.urls import reverse
from field_audit.models import AuditAction
from rest_framework.test import APIClient

from apps.api.v2.inspect.serializers import ChatbotInspectSerializer
from apps.api.v2.inspect.versioning import resolve_inspect_version
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.events.models import EventActionType
from apps.experiments.models import Experiment
from apps.files.models import File
from apps.teams.utils import current_team
from apps.utils.deletion import delete_object_with_auditing_of_related_objects
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.documents import CollectionFactory, CollectionFileFactory
from apps.utils.factories.events import EventActionFactory, StaticTriggerFactory, TimeoutTriggerFactory
from apps.utils.factories.experiment import (
    ChatbotFactory,
    ConsentFormFactory,
    ExperimentFactory,
    SourceMaterialFactory,
    SyntheticVoiceFactory,
)
from apps.utils.factories.files import FileFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import (
    AuthProviderFactory,
    EmbeddingProviderModelFactory,
    LlmProviderFactory,
    LlmProviderModelFactory,
    MessagingProviderFactory,
    TraceProviderFactory,
    VoiceProviderFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

# Distinctive secret values seeded into provider configs / channel extra_data. None of these may
# appear anywhere in the response.
LLM_SECRET = "sk-llm-SECRET-xyz"
AUTH_SECRET = "auth-SECRET-xyz"
CHANNEL_SECRET = "telegram-token-SECRET-xyz"


def _make_node(**kwargs):
    """Create a node and sync its resource FK mirror, mirroring production.

    ``NodeFactory.create`` only writes ``params``; production runs ``update_from_params()`` after
    every node write (via ``update_nodes_from_data``), which is what populates the FK/M2M columns and
    ``custom_action_operations`` the inspect serializers read.
    """
    node = NodeFactory.create(**kwargs)
    node.update_from_params()
    return node


@pytest.fixture(scope="module")
def inspect_bot(django_db_setup, django_db_blocker):
    """Build the bot once per module. Module-scoped fixtures can't request the function-scoped ``db``
    fixture, so unblock DB access directly and tear the team down once the module finishes."""
    with django_db_blocker.unblock():
        bot = _build_inspect_bot()
        yield bot
        with current_team(bot.experiment.team):
            delete_object_with_auditing_of_related_objects(bot.experiment.team)


def _build_inspect_bot():
    """Build a realistic bot that exercises every assertion here, returning the created objects so
    each test can build the exact expected response from their ids."""
    team = TeamWithUsersFactory.create()
    provider = LlmProviderFactory.create(team=team, name="Prod OpenAI", type="openai", config={"api_key": LLM_SECRET})
    model = LlmProviderModelFactory.create(team=team, name="gpt-4o", max_token_limit=128000, deprecated=False)

    collection = CollectionFactory.create(
        team=team,
        name="Policy index",
        is_index=True,
        embedding_provider_model=EmbeddingProviderModelFactory.create(team=team),
    )
    policy_file = FileFactory.create(team=team, name="policy.pdf", content_type="application/pdf")
    # FileFactory derives content_size from the (empty) file field; force a stable value via the DB.
    File.objects.filter(id=policy_file.id).update(content_size=40112)
    policy_file.refresh_from_db()
    CollectionFileFactory.create(collection=collection, file=policy_file)

    auth = AuthProviderFactory.create(team=team, config={"username": "u", "api_key": AUTH_SECRET})
    action = CustomActionFactory.create(team=team, name="Session Completion", auth_provider=auth)

    pipeline = PipelineFactory.create(team=team)
    _make_node(
        pipeline=pipeline,
        flow_id="router-1",
        type="RouterNode",
        label="Route",
        params={
            "name": "Route",
            "keywords": ["SCHEDULE", "RESCHEDULE"],
            "llm_provider_id": provider.id,
            "llm_provider_model_id": model.id,
            "prompt": "Route the user",
        },
    )
    _make_node(
        pipeline=pipeline,
        flow_id="answer-1",
        type="LLMResponseWithPrompt",
        label="Answer",
        params={
            "name": "Answer",
            "llm_provider_id": provider.id,
            "llm_provider_model_id": model.id,
            "collection_index_ids": [collection.id],
            "custom_actions": [f"{action.id}:weather_get"],
            "prompt": "Answer the user",
        },
    )
    experiment = ExperimentFactory.create(team=team, pipeline=pipeline)

    channel = ExperimentChannelFactory.create(
        team=team, experiment=experiment, name="Support TG", extra_data={"bot_token": CHANNEL_SECRET}
    )
    # Seed the team-global web/api channels; inspect now reads them rather than get_or_create-ing.
    ExperimentChannel.objects.get_team_web_channel(team)
    ExperimentChannel.objects.get_team_api_channel(team)
    timeout = TimeoutTriggerFactory.create(
        experiment=experiment,
        delay=86400,
        total_num_triggers=1,
        action=EventActionFactory.create(
            action_type=EventActionType.SEND_MESSAGE_TO_BOT, params={"message_to_bot": "Are you still there?"}
        ),
    )
    return SimpleNamespace(
        experiment=experiment,
        provider=provider,
        model=model,
        collection=collection,
        policy_file=policy_file,
        auth=auth,
        action=action,
        channel=channel,
        timeout=timeout,
    )


def _client(experiment, **kwargs):
    user = experiment.team.members.first()
    return ApiTestClient(user, experiment.team, **kwargs)


def _inspect_url(experiment):
    return reverse("api:v2:chatbot-inspect", kwargs={"id": experiment.public_id})


def _get(bot):
    return _client(bot.experiment).get(_inspect_url(bot.experiment)).json()


def _node(payload, label):
    return next(n for n in payload["pipeline"]["nodes"] if n["label"] == label)


def _expected_llm(bot):
    return {
        "provider_id": bot.provider.id,
        "provider_name": "Prod OpenAI",
        "type": "openai",
        "model": "gpt-4o",
        "max_token_limit": 128000,
        "deprecated": False,
    }


# ── Auth ─────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.django_db()
def test_anonymous_401(inspect_bot):
    assert APIClient().get(_inspect_url(inspect_bot.experiment)).status_code == 401


@pytest.mark.django_db()
def test_wrong_team_404(inspect_bot):
    other = TeamWithUsersFactory.create()
    client = _client(ExperimentFactory.create(team=other))
    assert client.get(_inspect_url(inspect_bot.experiment)).status_code == 404


@pytest.mark.django_db()
def test_read_only_key_allowed(inspect_bot):
    client = _client(inspect_bot.experiment, read_only=True)
    assert client.get(_inspect_url(inspect_bot.experiment)).status_code == 200


# ── Acceptance #1–#5 ───────────────────────────────────────────────────────────────────────────
@pytest.mark.django_db()
def test_acceptance_1_identity(inspect_bot):
    payload = _get(inspect_bot)
    assert payload["id"] == str(inspect_bot.experiment.public_id)
    assert payload["name"] == inspect_bot.experiment.name
    assert payload["is_unreleased"] is True
    assert payload["version_number"] == inspect_bot.experiment.version_number


@pytest.mark.django_db()
def test_acceptance_2_router_keywords(inspect_bot):
    assert _node(_get(inspect_bot), "Route")["params"] == {
        "keywords": ["SCHEDULE", "RESCHEDULE"],
        "prompt": "Route the user",
    }


@pytest.mark.django_db()
def test_acceptance_3_rag_collection_files(inspect_bot):
    assert _node(_get(inspect_bot), "Answer")["indexed_collections"] == [
        {
            "id": inspect_bot.collection.id,
            "name": "Policy index",
            "embedding": {
                "provider_id": inspect_bot.collection.llm_provider_id,
                "provider_name": inspect_bot.collection.llm_provider.name,
                "type": inspect_bot.collection.llm_provider.type,
                "model": "text-embedding-3-small",
            },
            "files": [
                {
                    "id": inspect_bot.policy_file.id,
                    "name": "policy.pdf",
                    "content_type": "application/pdf",
                    "content_size": 40112,
                    "external_source": "",
                    "external_id": "",
                }
            ],
        }
    ]


@pytest.mark.django_db()
def test_acceptance_4_timeout_trigger(inspect_bot):
    assert _get(inspect_bot)["events"]["timeout_triggers"] == [
        {
            "id": inspect_bot.timeout.id,
            "delay_seconds": 86400,
            "total_num_triggers": 1,
            "trigger_from_first_message": False,
            "is_active": True,
            "action": {"type": "send_message_to_bot", "params": {"message_to_bot": "Are you still there?"}},
        }
    ]


@pytest.mark.django_db()
def test_acceptance_5_custom_action_wired(inspect_bot):
    # wiring is implicit in containment — the action lives under the node that fires it (D10).
    # Only the operations selected on the node are rendered, not the action's full operation set.
    assert _node(_get(inspect_bot), "Answer")["custom_actions"] == [
        {
            "id": inspect_bot.action.id,
            "name": "Session Completion",
            "description": "Custom action description",
            "server_url": "https://api.weather.com",
            "allowed_operations": ["weather_get"],
            "api_schema": {"paths": ["/weather"]},
            "auth_provider": {
                "id": inspect_bot.auth.id,
                "type": "commcare",
                "name": inspect_bot.auth.name,
            },
        }
    ]


@pytest.mark.django_db()
def test_custom_action_unknown_operation_resolves_to_absent(inspect_bot):
    _make_node(
        pipeline=inspect_bot.experiment.pipeline,
        flow_id="stale-1",
        type="LLMResponseWithPrompt",
        label="Stale",
        params={"custom_actions": [f"{inspect_bot.action.id}:no_such_op"]},
    )
    # A selected operation that no longer exists in the action's schema renders as absent.
    assert _node(_get(inspect_bot), "Stale")["custom_actions"] == [
        {
            "id": inspect_bot.action.id,
            "name": "Session Completion",
            "description": "Custom action description",
            "server_url": "https://api.weather.com",
            "allowed_operations": [],
            "api_schema": {"paths": []},
            "auth_provider": {
                "id": inspect_bot.auth.id,
                "type": "commcare",
                "name": inspect_bot.auth.name,
            },
        }
    ]


# ── Secret exclusion ───────────────────────────────────────────────────────────────────────────
@pytest.mark.django_db()
def test_no_secrets_in_response(inspect_bot):
    body = _client(inspect_bot.experiment).get(_inspect_url(inspect_bot.experiment)).content.decode()
    for secret in (LLM_SECRET, AUTH_SECRET, CHANNEL_SECRET):
        assert secret not in body
    for excluded_key in ("config", "extra_data", "api_key", "bot_token"):
        assert excluded_key not in body


@pytest.mark.django_db()
def test_inspect_does_not_create_team_channels(inspect_bot):
    """Inspecting must not change anything: it reads the team's web/api channels but must never
    create them."""
    team = inspect_bot.experiment.team
    ExperimentChannel.objects.filter(team=team, platform__in=[ChannelPlatform.WEB, ChannelPlatform.API]).delete(
        audit_action=AuditAction.AUDIT
    )

    channels = _get(inspect_bot)["channels"]

    assert [c["platform"] for c in channels] == ["telegram"]
    assert not ExperimentChannel.objects.filter(
        team=team, platform__in=[ChannelPlatform.WEB, ChannelPlatform.API]
    ).exists()


# ── Cross-team isolation ─────────────────────────────────────────────────────────────────────────
# The inspect serializers now read the node's resource FK/M2M relations directly (a derived mirror of
# params), rather than re-loading each id through a team-scoped fetcher. The read-time defense-in-depth
# that nulled cross-team ids is therefore gone by design: team isolation is enforced where params are
# written (a node's params must never hold another team's id), not at inspect read. The former
# read-time cross-team tests were removed with the fetcher; see the follow-up ADR superseding the
# read-time-scoping aspect of ADR-0028.


@pytest.mark.django_db()
def test_malformed_node_param_id_does_not_crash(inspect_bot):
    """A non-numeric id in a node's params resolves to nothing instead of crashing the response."""
    _make_node(
        pipeline=inspect_bot.experiment.pipeline,
        flow_id="malformed-1",
        type="LLMResponseWithPrompt",
        label="Malformed",
        params={"llm_provider_id": "abc", "source_material_id": "not-an-int"},
    )
    node = _node(_get(inspect_bot), "Malformed")
    # decision #5: LLMResponseWithPrompt declares these keys, so a malformed id renders null
    # (the key is present) rather than being omitted.
    assert node["source_material"] is None
    assert node["llm"] is None


# ── Inline shape / dedup ─────────────────────────────────────────────────────────────────────────
@pytest.mark.django_db()
def test_shared_provider_inlined_identically(inspect_bot):
    payload = _get(inspect_bot)
    # the same LlmProvider feeds both nodes: byte-identical, same id at each site (ADR-0025)
    assert _node(payload, "Route")["llm"] == _node(payload, "Answer")["llm"] == _expected_llm(inspect_bot)


# ── Versioning ───────────────────────────────────────────────────────────────────────────────────
@pytest.mark.django_db()
def test_version_default_and_specific(inspect_bot):
    version = inspect_bot.experiment.create_new_version()  # version_number 1, published default
    client = _client(inspect_bot.experiment)
    url = _inspect_url(inspect_bot.experiment)

    assert client.get(url).json()["is_unreleased"] is True

    specific = client.get(f"{url}?version={version.version_number}").json()
    assert specific["version_number"] == version.version_number
    assert specific["is_unreleased"] is False

    assert client.get(f"{url}?version=default").json()["version_number"] == version.version_number


@pytest.mark.django_db()
def test_unknown_version_404(inspect_bot):
    url = _inspect_url(inspect_bot.experiment)
    assert _client(inspect_bot.experiment).get(f"{url}?version=999").status_code == 404


# ── Full response body ───────────────────────────────────────────────────────────────────────────
def _full_bot():
    """A fully populated bot whose response has no null fields, so every serializer runs.

    The pipeline node params store ids as strings, matching what the React frontend saves.
    """
    team = TeamWithUsersFactory.create()

    llm_provider = LlmProviderFactory.create(team=team, name="Prod OpenAI", type="openai")
    llm_model = LlmProviderModelFactory.create(team=team, name="gpt-4o", max_token_limit=128000, deprecated=False)
    embedding_model = EmbeddingProviderModelFactory.create(team=team, name="text-embedding-3-small")
    voice_provider = VoiceProviderFactory.create(team=team, name="ElevenLabs Prod")
    trace_provider = TraceProviderFactory.create(team=team, name="Langfuse Prod")
    auth_provider = AuthProviderFactory.create(team=team, name="Partner Auth")
    messaging_provider = MessagingProviderFactory.create(team=team, name="Twilio Prod")

    synthetic_voice = SyntheticVoiceFactory.create(
        name="Rachel", language="English", neural=True, voice_provider=voice_provider
    )
    consent = ConsentFormFactory.create(
        team=team,
        name="Default consent",
        consent_text="Do you agree?",
        capture_identifier=True,
        identifier_label="Email",
        identifier_type="email",
    )
    source = SourceMaterialFactory.create(
        team=team, topic="Returns", description="Returns policy", material="# Returns"
    )

    media_collection = CollectionFactory.create(
        team=team, name="Media docs", is_index=False, llm_provider=None, embedding_provider_model=None
    )
    media_file = FileFactory.create(team=team, name="guide.pdf", content_type="application/pdf", purpose="collection")
    File.objects.filter(id=media_file.id).update(content_size=50321)
    CollectionFileFactory.create(collection=media_collection, file=media_file)

    index_collection = CollectionFactory.create(
        team=team,
        name="Policy index",
        is_index=True,
        llm_provider=llm_provider,
        embedding_provider_model=embedding_model,
    )
    index_file = FileFactory.create(team=team, name="policy.pdf", content_type="application/pdf", purpose="collection")
    File.objects.filter(id=index_file.id).update(content_size=40112)
    CollectionFileFactory.create(collection=index_collection, file=index_file)

    action = CustomActionFactory.create(
        team=team,
        name="Session Completion",
        server_url="https://api.weather.com",
        allowed_operations=["weather_get"],
        auth_provider=auth_provider,
    )
    assistant = OpenAiAssistantFactory.create(
        team=team, name="Helper", assistant_id="asst_123", instructions="Be helpful", temperature=1.0, top_p=1.0
    )

    pipeline = PipelineFactory.create(
        team=team,
        name="Support flow",
        data={
            "nodes": [
                {
                    "id": "llm",
                    "data": {
                        "id": "llm",
                        "type": "LLMResponseWithPrompt",
                        "label": "Answer",
                        # the react FE persists ids as strings
                        "params": {
                            "llm_provider_id": str(llm_provider.id),
                            "llm_provider_model_id": str(llm_model.id),
                            "source_material_id": str(source.id),
                            "collection_id": str(media_collection.id),
                            "collection_index_ids": [str(index_collection.id)],
                            "custom_actions": [f"{action.id}:weather_get", f"{action.id}:pollen_get"],
                            "synthetic_voice_id": str(synthetic_voice.id),
                            "prompt": "Answer the user",
                        },
                    },
                },
                {
                    "id": "assist",
                    "data": {
                        "id": "assist",
                        "type": "AssistantNode",
                        "label": "Assistant",
                        "params": {"assistant_id": str(assistant.id), "citations_enabled": True},
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "llm", "target": "assist", "sourceHandle": "output", "targetHandle": "input"}
            ],
        },
    )

    experiment = ChatbotFactory.create(
        team=team,
        name="Support Bot",
        pipeline=pipeline,
        description="Customer support bot",
        seed_message="Welcome",
        participant_allowlist=["+27123"],
        consent_form=consent,
        voice_provider=voice_provider,
        synthetic_voice=synthetic_voice,
        trace_provider=trace_provider,
    )
    ExperimentChannelFactory.create(
        team=team, experiment=experiment, name="Support TG", messaging_provider=messaging_provider
    )
    # Seed the team-global web/api channels; inspect now reads them rather than get_or_create-ing.
    ExperimentChannel.objects.get_team_web_channel(team)
    ExperimentChannel.objects.get_team_api_channel(team)
    schedule_trigger = StaticTriggerFactory.create(
        experiment=experiment,
        type="conversation_start",
        action=EventActionFactory.create(
            action_type=EventActionType.SCHEDULETRIGGER,
            params={
                "name": "Daily nudge",
                "frequency": 1,
                "time_period": "days",
                "repetitions": 3,
                "prompt_text": "Hi",
            },
        ),
    )
    timeout_trigger = TimeoutTriggerFactory.create(
        experiment=experiment,
        delay=86400,
        total_num_triggers=1,
        action=EventActionFactory.create(
            action_type=EventActionType.SEND_MESSAGE_TO_BOT, params={"message_to_bot": "Still there?"}
        ),
    )
    return SimpleNamespace(
        team=team,
        experiment=experiment,
        pipeline=pipeline,
        llm_provider=llm_provider,
        llm_model=llm_model,
        voice_provider=voice_provider,
        trace_provider=trace_provider,
        auth_provider=auth_provider,
        messaging_provider=messaging_provider,
        synthetic_voice=synthetic_voice,
        consent=consent,
        source=source,
        media_collection=media_collection,
        media_file=media_file,
        index_collection=index_collection,
        index_file=index_file,
        action=action,
        assistant=assistant,
        schedule_trigger=schedule_trigger,
        timeout_trigger=timeout_trigger,
    )


@pytest.mark.django_db()
def test_full_response_body():
    """A fully populated bot whose response has no null fields, so every serializer runs."""
    bot = _full_bot()

    payload = _get(bot)

    assert payload == {
        "id": str(bot.experiment.public_id),
        "name": "Support Bot",
        "description": "Customer support bot",
        "version_number": bot.experiment.version_number,
        "is_unreleased": True,
        "is_published_version": False,
        "version_description": "",
        "team_slug": bot.team.slug,
        "settings": {
            "seed_message": "Welcome",
            "conversational_consent_enabled": False,
            "voice_response_behaviour": "reciprocal",
            "echo_transcript": True,
            "debug_mode_enabled": False,
            "file_uploads_enabled": False,
            "participant_allowlist": ["+27123"],
        },
        "consent_form": {
            "id": bot.consent.id,
            "name": "Default consent",
            "consent_text": "Do you agree?",
            "capture_identifier": True,
            "identifier_label": "Email",
            "identifier_type": "email",
        },
        "voice": {
            "provider_id": bot.voice_provider.id,
            "provider_name": "ElevenLabs Prod",
            "type": bot.voice_provider.type,
            "voice_name": "Rachel",
            "language": "English",
            "neural": True,
        },
        "trace_provider": {"id": bot.trace_provider.id, "type": bot.trace_provider.type, "name": "Langfuse Prod"},
        "channels": [
            {
                "platform": "telegram",
                "name": "Support TG",
                "messaging_provider": {
                    "id": bot.messaging_provider.id,
                    "type": bot.messaging_provider.type,
                    "name": "Twilio Prod",
                },
            },
            {"platform": "web", "name": f"{bot.team.slug}-web-channel", "messaging_provider": None},
            {"platform": "api", "name": f"{bot.team.slug}-api-channel", "messaging_provider": None},
        ],
        "pipeline": {
            "id": bot.pipeline.id,
            "name": "Support flow",
            "version_number": bot.pipeline.version_number,
            "graph": {
                "nodes": [
                    {"node_id": "llm", "type": "LLMResponseWithPrompt", "label": "Answer"},
                    {"node_id": "assist", "type": "AssistantNode", "label": "Assistant"},
                ],
                "edges": [{"source": "llm", "target": "assist", "source_handle": "output", "target_handle": "input"}],
            },
            "nodes": [
                {
                    "node_id": "llm",
                    "type": "LLMResponseWithPrompt",
                    "label": "Answer",
                    "params": {"prompt": "Answer the user"},
                    "llm": {
                        "provider_id": bot.llm_provider.id,
                        "provider_name": "Prod OpenAI",
                        "type": "openai",
                        "model": "gpt-4o",
                        "max_token_limit": 128000,
                        "deprecated": False,
                    },
                    "source_material": {
                        "id": bot.source.id,
                        "topic": "Returns",
                        "description": "Returns policy",
                        "material": "# Returns",
                    },
                    "media_collection": {
                        "id": bot.media_collection.id,
                        "name": "Media docs",
                        "files": [
                            {
                                "id": bot.media_file.id,
                                "name": "guide.pdf",
                                "content_type": "application/pdf",
                                "content_size": 50321,
                                "external_source": "",
                                "external_id": "",
                            }
                        ],
                    },
                    "indexed_collections": [
                        {
                            "id": bot.index_collection.id,
                            "name": "Policy index",
                            "embedding": {
                                "provider_id": bot.llm_provider.id,
                                "provider_name": "Prod OpenAI",
                                "type": "openai",
                                "model": "text-embedding-3-small",
                            },
                            "files": [
                                {
                                    "id": bot.index_file.id,
                                    "name": "policy.pdf",
                                    "content_type": "application/pdf",
                                    "content_size": 40112,
                                    "external_source": "",
                                    "external_id": "",
                                }
                            ],
                        }
                    ],
                    "custom_actions": [
                        {
                            "id": bot.action.id,
                            "name": "Session Completion",
                            "description": "Custom action description",
                            "server_url": "https://api.weather.com",
                            "allowed_operations": ["weather_get", "pollen_get"],
                            "api_schema": {"paths": ["/pollen", "/weather"]},
                            "auth_provider": {
                                "id": bot.auth_provider.id,
                                "type": bot.auth_provider.type,
                                "name": "Partner Auth",
                            },
                        }
                    ],
                    "voice": {
                        "provider_id": bot.voice_provider.id,
                        "provider_name": "ElevenLabs Prod",
                        "type": bot.voice_provider.type,
                        "voice_name": "Rachel",
                        "language": "English",
                        "neural": True,
                    },
                },
                {
                    "node_id": "assist",
                    "type": "AssistantNode",
                    "label": "Assistant",
                    "params": {"citations_enabled": True},
                    "assistant": {
                        "id": bot.assistant.id,
                        "name": "Helper",
                        "assistant_id": "asst_123",
                        "instructions": "Be helpful",
                        "builtin_tools": [],
                        "tools": [],
                        "temperature": 1.0,
                        "top_p": 1.0,
                    },
                },
            ],
        },
        "events": {
            "static_triggers": [
                {
                    "id": bot.schedule_trigger.id,
                    "type": "conversation_start",
                    "is_active": True,
                    "action": {
                        "type": "schedule_trigger",
                        "params": {
                            "scheduled_message": {
                                "name": "Daily nudge",
                                "frequency": 1,
                                "time_period": "days",
                                "repetitions": 3,
                                "prompt_text": "Hi",
                            },
                        },
                    },
                }
            ],
            "timeout_triggers": [
                {
                    "id": bot.timeout_trigger.id,
                    "delay_seconds": 86400,
                    "total_num_triggers": 1,
                    "trigger_from_first_message": False,
                    "is_active": True,
                    "action": {"type": "send_message_to_bot", "params": {"message_to_bot": "Still there?"}},
                }
            ],
        },
    }


# ── Embedded pipeline (pipeline_start) + context propagation (review issue #6) ──────────────────
@pytest.mark.django_db()
def test_pipeline_start_trigger_embeds_resource_bearing_pipeline(inspect_bot):
    """A pipeline_start trigger embeds a second pipeline whose node references a resource — proving
    the embedded pipeline serializer resolves the node's FK relations like the main pipeline does."""
    team = inspect_bot.experiment.team
    assistant = OpenAiAssistantFactory.create(team=team, name="Embedded Helper", assistant_id="asst_embed")
    embedded = PipelineFactory.create(team=team, data={"nodes": [], "edges": []})
    _make_node(
        pipeline=embedded,
        flow_id="emb-1",
        type="AssistantNode",
        label="Embedded",
        params={"assistant_id": str(assistant.id)},
    )
    StaticTriggerFactory.create(
        experiment=inspect_bot.experiment,
        type="conversation_start",
        action=EventActionFactory.create(
            action_type=EventActionType.PIPELINE_START, params={"pipeline_id": str(embedded.id)}
        ),
    )

    payload = _get(inspect_bot)

    action = next(t["action"] for t in payload["events"]["static_triggers"] if t["action"]["type"] == "pipeline_start")
    assert "pipeline_id" not in action["params"]
    embedded_node = next(n for n in action["pipeline"]["nodes"] if n["label"] == "Embedded")
    assert embedded_node["assistant"]["assistant_id"] == "asst_embed"


# ── Query-count guard (review issues #5, #12, #16) ───────────────────────────────────────────────
def _adversarial_bot():
    """A multi-node pipeline, a pipeline_start trigger that embeds a second pipeline with its own
    resources, and one LLM provider/model shared by two nodes (so a shared resource loads once)."""
    team = TeamWithUsersFactory.create()
    provider = LlmProviderFactory.create(team=team, name="Shared", type="openai")
    model = LlmProviderModelFactory.create(team=team, name="gpt-4o", deprecated=False)
    source = SourceMaterialFactory.create(team=team)
    assistant = OpenAiAssistantFactory.create(team=team)

    pipeline = PipelineFactory.create(team=team, data={"nodes": [], "edges": []})
    _make_node(
        pipeline=pipeline,
        flow_id="n1",
        type="LLMResponseWithPrompt",
        label="A",
        params={
            "llm_provider_id": str(provider.id),
            "llm_provider_model_id": str(model.id),
            "source_material_id": str(source.id),
        },
    )
    _make_node(
        pipeline=pipeline,
        flow_id="n2",
        type="RouterNode",
        label="B",
        params={"llm_provider_id": str(provider.id), "llm_provider_model_id": str(model.id), "keywords": ["X"]},
    )
    experiment = ExperimentFactory.create(team=team, pipeline=pipeline)

    embedded = PipelineFactory.create(team=team, data={"nodes": [], "edges": []})
    _make_node(
        pipeline=embedded,
        flow_id="e1",
        type="AssistantNode",
        label="Embedded",
        params={"assistant_id": str(assistant.id)},
    )
    StaticTriggerFactory.create(
        experiment=experiment,
        type="conversation_start",
        action=EventActionFactory.create(
            action_type=EventActionType.PIPELINE_START, params={"pipeline_id": str(embedded.id)}
        ),
    )
    return experiment


# Empirically derived. The SAME number must hold for every version mode, because resolve_inspect_version
# resolves AND prefetches the target with a fixed prefetch set — including each node's resource FK/M2M
# relations (inspect_node_queryset) — so node fan-out adds no queries. Resolution costs one query in
# every mode, so folding it into the measured block keeps the count constant.
EXPECTED_RENDER_QUERIES = 13


@pytest.mark.django_db()
@pytest.mark.parametrize("version_param", [None, "default", "1"])
def test_inspect_render_query_count_constant_across_versions(version_param, django_assert_num_queries):
    """Resolving, fetching and rendering takes the same fixed number of queries for every version
    mode — the working draft, the default, or a specific version number."""
    experiment = _adversarial_bot()
    experiment.create_new_version()  # version_number 1, published default
    family = Experiment.objects.get(pk=experiment.pk)
    team = family.team  # the view already holds request.team; read it outside the measured block

    with django_assert_num_queries(EXPECTED_RENDER_QUERIES):
        target = resolve_inspect_version(family.public_id, version_param, team=team)
        data = ChatbotInspectSerializer(target, context={"team": target.team}).data
        json.dumps(data)  # force lazy rendering of every nested serializer
