from types import SimpleNamespace

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.events.models import EventActionType
from apps.files.models import File
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
    SurveyFactory,
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


@pytest.fixture()
def inspect_bot(db):
    """A realistic bot exercising every acceptance assertion. Returns the created objects so tests
    can build exact expected dicts referencing their ids."""
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
    NodeFactory.create(
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
    NodeFactory.create(
        pipeline=pipeline,
        flow_id="answer-1",
        type="LLMResponseWithPrompt",
        label="Answer",
        params={
            "name": "Answer",
            "llm_provider_id": provider.id,
            "llm_provider_model_id": model.id,
            "collection_index_ids": [collection.id],
            "custom_actions": [f"{action.id}:complete_session"],
            "prompt": "Answer the user",
        },
    )
    experiment = ExperimentFactory.create(team=team, pipeline=pipeline)

    channel = ExperimentChannelFactory.create(
        team=team, experiment=experiment, name="Support TG", extra_data={"bot_token": CHANNEL_SECRET}
    )
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
                    "purpose": inspect_bot.policy_file.purpose,
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
            "action": {"type": "send_message_to_bot", "message_to_bot": "Are you still there?"},
        }
    ]


@pytest.mark.django_db()
def test_acceptance_5_custom_action_wired(inspect_bot):
    # wiring is implicit in containment — the action lives under the node that fires it (D10)
    assert _node(_get(inspect_bot), "Answer")["custom_actions"] == [
        {
            "id": inspect_bot.action.id,
            "name": "Session Completion",
            "description": "Custom action description",
            "server_url": "https://api.weather.com",
            "allowed_operations": ["weather_get"],
            "api_schema": {"paths": ["/pollen", "/weather"]},
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
def test_channel_allowlisted(inspect_bot):
    assert _get(inspect_bot)["channels"] == [{"platform": "telegram", "name": "Support TG", "messaging_provider": None}]


# ── Cross-team isolation ─────────────────────────────────────────────────────────────────────────
@pytest.mark.django_db()
def test_cross_team_resource_not_embedded(inspect_bot):
    foreign_collection = CollectionFactory.create(team=TeamWithUsersFactory.create(), is_index=True)
    NodeFactory.create(
        pipeline=inspect_bot.experiment.pipeline,
        flow_id="leaky-1",
        type="LLMResponseWithPrompt",
        label="Leaky",
        params={"collection_index_ids": [foreign_collection.id]},
    )
    # A cross-team collection id resolves to absent rather than leaking another team's resource.
    assert _node(_get(inspect_bot), "Leaky")["indexed_collections"] == []


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
@pytest.mark.django_db()
def test_full_response_body():
    """A fully-populated bot whose response has no null fields — exercises every serializer."""
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
    pre_survey = SurveyFactory.create(team=team, name="Pre", url="https://pre", confirmation_text="thanks-pre")
    post_survey = SurveyFactory.create(team=team, name="Post", url="https://post", confirmation_text="thanks-post")
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
                        "params": {
                            "llm_provider_id": llm_provider.id,
                            "llm_provider_model_id": llm_model.id,
                            "source_material_id": source.id,
                            "collection_id": media_collection.id,
                            "collection_index_ids": [index_collection.id],
                            "custom_actions": [f"{action.id}:complete_session"],
                            "synthetic_voice_id": synthetic_voice.id,
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
                        "params": {"assistant_id": assistant.id, "citations_enabled": True},
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
        pre_survey=pre_survey,
        post_survey=post_survey,
        voice_provider=voice_provider,
        synthetic_voice=synthetic_voice,
        trace_provider=trace_provider,
    )
    ExperimentChannelFactory.create(
        team=team, experiment=experiment, name="Support TG", messaging_provider=messaging_provider
    )
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

    media_file_dict = {
        "id": media_file.id,
        "name": "guide.pdf",
        "content_type": "application/pdf",
        "content_size": 50321,
        "external_source": "",
        "external_id": "",
        "purpose": "collection",
    }
    index_file_dict = {**media_file_dict, "id": index_file.id, "name": "policy.pdf", "content_size": 40112}
    llm_concept = {
        "provider_id": llm_provider.id,
        "provider_name": "Prod OpenAI",
        "type": "openai",
        "model": "gpt-4o",
        "max_token_limit": 128000,
        "deprecated": False,
    }
    voice_concept = {
        "provider_id": voice_provider.id,
        "provider_name": "ElevenLabs Prod",
        "type": voice_provider.type,
        "voice_name": "Rachel",
        "language": "English",
        "neural": True,
    }

    payload = _client(experiment).get(_inspect_url(experiment)).json()

    assert payload == {
        "id": str(experiment.public_id),
        "name": "Support Bot",
        "description": "Customer support bot",
        "version_number": experiment.version_number,
        "is_unreleased": True,
        "is_published_version": False,
        "version_description": "",
        "team_slug": team.slug,
        "settings": {
            "seed_message": "Welcome",
            "conversational_consent_enabled": False,
            "voice_response_behaviour": "reciprocal",
            "echo_transcript": True,
            "use_processor_bot_voice": False,
            "debug_mode_enabled": False,
            "file_uploads_enabled": False,
            "participant_allowlist": ["+27123"],
        },
        "consent_form": {
            "id": consent.id,
            "name": "Default consent",
            "consent_text": "Do you agree?",
            "capture_identifier": True,
            "identifier_label": "Email",
            "identifier_type": "email",
        },
        "pre_survey": {"id": pre_survey.id, "name": "Pre", "url": "https://pre", "confirmation_text": "thanks-pre"},
        "post_survey": {
            "id": post_survey.id,
            "name": "Post",
            "url": "https://post",
            "confirmation_text": "thanks-post",
        },
        "voice": voice_concept,
        "trace_provider": {"id": trace_provider.id, "type": trace_provider.type, "name": "Langfuse Prod"},
        "channels": [
            {
                "platform": "telegram",
                "name": "Support TG",
                "messaging_provider": {
                    "id": messaging_provider.id,
                    "type": messaging_provider.type,
                    "name": "Twilio Prod",
                },
            }
        ],
        "pipeline": {
            "id": pipeline.id,
            "name": "Support flow",
            "version_number": pipeline.version_number,
            "graph": {
                "nodes": [
                    {"flow_id": "llm", "type": "LLMResponseWithPrompt", "label": "Answer"},
                    {"flow_id": "assist", "type": "AssistantNode", "label": "Assistant"},
                ],
                "edges": [{"source": "llm", "target": "assist", "source_handle": "output", "target_handle": "input"}],
            },
            "nodes": [
                {
                    "flow_id": "llm",
                    "type": "LLMResponseWithPrompt",
                    "label": "Answer",
                    "params": {"prompt": "Answer the user"},
                    "llm": llm_concept,
                    "source_material": {
                        "id": source.id,
                        "topic": "Returns",
                        "description": "Returns policy",
                        "material": "# Returns",
                    },
                    "media_collection": {
                        "id": media_collection.id,
                        "name": "Media docs",
                        "files": [media_file_dict],
                    },
                    "indexed_collections": [
                        {
                            "id": index_collection.id,
                            "name": "Policy index",
                            "embedding": {
                                "provider_id": llm_provider.id,
                                "provider_name": "Prod OpenAI",
                                "type": "openai",
                                "model": "text-embedding-3-small",
                            },
                            "files": [index_file_dict],
                        }
                    ],
                    "custom_actions": [
                        {
                            "id": action.id,
                            "name": "Session Completion",
                            "description": "Custom action description",
                            "server_url": "https://api.weather.com",
                            "allowed_operations": ["weather_get"],
                            "api_schema": {"paths": ["/pollen", "/weather"]},
                            "auth_provider": {
                                "id": auth_provider.id,
                                "type": auth_provider.type,
                                "name": "Partner Auth",
                            },
                        }
                    ],
                    "voice": voice_concept,
                },
                {
                    "flow_id": "assist",
                    "type": "AssistantNode",
                    "label": "Assistant",
                    "params": {"citations_enabled": True},
                    "assistant": {
                        "id": assistant.id,
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
                    "id": schedule_trigger.id,
                    "type": "conversation_start",
                    "is_active": True,
                    "action": {
                        "type": "schedule_trigger",
                        "scheduled_message": {
                            "name": "Daily nudge",
                            "frequency": 1,
                            "time_period": "days",
                            "repetitions": 3,
                            "prompt_text": "Hi",
                        },
                    },
                }
            ],
            "timeout_triggers": [
                {
                    "id": timeout_trigger.id,
                    "delay_seconds": 86400,
                    "total_num_triggers": 1,
                    "trigger_from_first_message": False,
                    "is_active": True,
                    "action": {"type": "send_message_to_bot", "message_to_bot": "Still there?"},
                }
            ],
        },
    }
