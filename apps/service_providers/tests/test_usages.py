import pytest
from django.urls import reverse

from apps.service_providers.models import LlmProviderTypes, MessagingProviderType, TraceProviderType, VoiceProviderType
from apps.service_providers.usages import get_provider_usages, search_providers_by_api_key
from apps.service_providers.utils import ServiceProvider
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    MessagingProviderFactory,
    TraceProviderFactory,
    VoiceProviderFactory,
)


@pytest.fixture()
def anthropic_provider(team_with_users):
    return LlmProviderFactory(
        team=team_with_users,
        type=str(LlmProviderTypes.anthropic),
        config={"anthropic_api_key": "sk-ant-secret-AbCdEf", "anthropic_api_base": "https://api.anthropic.com"},
    )


@pytest.mark.django_db()
def test_get_usages_includes_assistants(anthropic_provider):
    OpenAiAssistantFactory(team=anthropic_provider.team, llm_provider=anthropic_provider)

    usages = get_provider_usages(anthropic_provider)

    category_labels = {c.label for c in usages.categories}
    assert any("Assistant" in label for label in category_labels)
    assert not usages.is_empty()
    assert usages.total >= 1


@pytest.mark.django_db()
def test_get_usages_empty_when_unreferenced(anthropic_provider):
    usages = get_provider_usages(anthropic_provider)
    assert usages.is_empty()
    assert usages.total == 0


@pytest.mark.django_db()
def test_usages_view_handles_versioned_object_without_version_number(team_with_users, client):
    """Regression: VersionsMixin subclasses that don't declare ``version_number``
    (e.g. DocumentSource) used to crash the usages page because the template
    called ``get_version_name`` which assumed the field existed."""
    from apps.utils.factories.documents import DocumentSourceFactory  # noqa: PLC0415
    from apps.utils.factories.service_provider_factories import AuthProviderFactory  # noqa: PLC0415

    auth_provider = AuthProviderFactory(team=team_with_users)
    working = DocumentSourceFactory(team=team_with_users, auth_provider=auth_provider)
    DocumentSourceFactory(team=team_with_users, auth_provider=auth_provider, working_version=working)

    user = team_with_users.members.first()
    client.force_login(user)
    response = client.get(
        reverse(
            "service_providers:usages",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "auth", "pk": auth_provider.pk},
        )
    )
    assert response.status_code == 200


@pytest.mark.django_db()
def test_messaging_channels_roll_up_to_chatbots(team_with_users):
    from apps.utils.factories.channels import ExperimentChannelFactory  # noqa: PLC0415

    messaging = MessagingProviderFactory(team=team_with_users)
    chatbot = ExperimentFactory(team=team_with_users)
    ExperimentChannelFactory(team=team_with_users, experiment=chatbot, messaging_provider=messaging)
    ExperimentChannelFactory(team=team_with_users, experiment=chatbot, messaging_provider=messaging)
    # Channel with no chatbot.
    ExperimentChannelFactory(team=team_with_users, experiment=None, messaging_provider=messaging)

    usages = get_provider_usages(messaging)

    labels = {c.label for c in usages.categories}
    assert labels == {"Chatbots", "Unlinked Channels"}

    chatbots_cat = next(c for c in usages.categories if c.kind == "chatbots_with_channels")
    assert len(chatbots_cat.items) == 1
    entry = chatbots_cat.items[0]
    assert entry["chatbot"].id == chatbot.id
    assert len(entry["channels"]) == 2

    unlinked = next(c for c in usages.categories if c.label == "Unlinked Channels")
    assert len(unlinked.items) == 1


@pytest.mark.django_db()
def test_experiment_category_displays_as_chatbots(team_with_users):
    voice = VoiceProviderFactory(team=team_with_users)
    ExperimentFactory(team=team_with_users, voice_provider=voice)

    usages = get_provider_usages(voice)

    category_labels = {c.label for c in usages.categories}
    assert "Chatbots" in category_labels
    assert "Experiments" not in category_labels


@pytest.mark.django_db()
def test_pipeline_chatbots_via_event_configuration(anthropic_provider):
    from apps.events.models import EventActionType  # noqa: PLC0415 — keep test imports local
    from apps.utils.factories.events import EventActionFactory, StaticTriggerFactory  # noqa: PLC0415
    from apps.utils.factories.pipelines import NodeFactory, PipelineFactory  # noqa: PLC0415

    team = anthropic_provider.team
    pipeline = PipelineFactory(team=team)
    NodeFactory(pipeline=pipeline, type="LLMResponseWithPrompt", params={"llm_provider_id": anthropic_provider.id})

    # Experiment is NOT directly linked to the pipeline; it triggers it via an event.
    indirect_experiment = ExperimentFactory(team=team, pipeline=None)
    action = EventActionFactory(
        action_type=EventActionType.PIPELINE_START,
        params={"pipeline_id": pipeline.id},
    )
    StaticTriggerFactory(experiment=indirect_experiment, action=action)

    usages = get_provider_usages(anthropic_provider)

    chatbot_categories = [c for c in usages.categories if c.kind == "chatbots_with_pipelines"]
    assert chatbot_categories, "expected a chatbots-with-pipelines category"
    entry = next(item for item in chatbot_categories[0].items if item["chatbot"].id == indirect_experiment.id)
    assert pipeline in entry["pipelines"]


@pytest.mark.django_db()
def test_pipelines_without_chatbots_appear_in_unlinked_category(anthropic_provider):
    from apps.utils.factories.pipelines import NodeFactory, PipelineFactory  # noqa: PLC0415

    team = anthropic_provider.team
    lonely_pipeline = PipelineFactory(team=team, name="Lonely")
    NodeFactory(
        pipeline=lonely_pipeline,
        type="LLMResponseWithPrompt",
        params={"llm_provider_id": anthropic_provider.id},
    )

    linked_pipeline = PipelineFactory(team=team, name="Linked")
    NodeFactory(
        pipeline=linked_pipeline,
        type="LLMResponseWithPrompt",
        params={"llm_provider_id": anthropic_provider.id},
    )
    ExperimentFactory(team=team, pipeline=linked_pipeline)

    usages = get_provider_usages(anthropic_provider)

    labels = {c.label for c in usages.categories}
    assert "Chatbots" in labels
    assert "Unlinked Pipelines" in labels

    unlinked = next(c for c in usages.categories if c.kind == "pipelines")
    assert [p.id for p in unlinked.items] == [lonely_pipeline.id]


@pytest.mark.django_db()
def test_pipeline_chatbots_dedupe_direct_and_event_links(anthropic_provider):
    from apps.events.models import EventActionType  # noqa: PLC0415
    from apps.utils.factories.events import EventActionFactory, StaticTriggerFactory  # noqa: PLC0415
    from apps.utils.factories.pipelines import NodeFactory, PipelineFactory  # noqa: PLC0415

    team = anthropic_provider.team
    pipeline = PipelineFactory(team=team)
    NodeFactory(pipeline=pipeline, type="LLMResponseWithPrompt", params={"llm_provider_id": anthropic_provider.id})

    experiment = ExperimentFactory(team=team, pipeline=pipeline)
    action = EventActionFactory(
        action_type=EventActionType.PIPELINE_START,
        params={"pipeline_id": str(pipeline.id)},  # also exercises the str-id fallback
    )
    StaticTriggerFactory(experiment=experiment, action=action)

    usages = get_provider_usages(anthropic_provider)

    chatbot_categories = [c for c in usages.categories if c.kind == "chatbots_with_pipelines"]
    assert len(chatbot_categories) == 1
    entries = chatbot_categories[0].items
    assert len(entries) == 1, "direct + event-driven references to the same chatbot should dedupe"
    assert entries[0]["chatbot"].id == experiment.id
    assert [p.id for p in entries[0]["pipelines"]] == [pipeline.id]


@pytest.mark.django_db()
def test_search_exact_match_finds_provider(anthropic_provider):
    matches = search_providers_by_api_key(ServiceProvider.llm, "sk-ant-secret-AbCdEf", match="exact")
    assert anthropic_provider in matches


@pytest.mark.django_db()
def test_search_suffix_match(anthropic_provider):
    matches = search_providers_by_api_key(ServiceProvider.llm, "AbCdEf", match="suffix")
    assert anthropic_provider in matches


@pytest.mark.django_db()
def test_search_contains_match(anthropic_provider):
    matches = search_providers_by_api_key(ServiceProvider.llm, "secret", match="contains")
    assert anthropic_provider in matches


@pytest.mark.django_db()
def test_search_no_false_positive_on_other_subtype(team_with_users, anthropic_provider):
    LlmProviderFactory(
        team=team_with_users,
        type=str(LlmProviderTypes.openai),
        config={"openai_api_key": "sk-ant-secret-AbCdEf"},
    )
    matches = search_providers_by_api_key(ServiceProvider.llm, "sk-ant-secret-AbCdEf", match="exact")
    # both should match — both contain the value in *their* declared obfuscate fields
    assert len(matches) == 2


@pytest.mark.django_db()
def test_search_ignores_provider_with_different_secret(anthropic_provider):
    LlmProviderFactory(
        team=anthropic_provider.team,
        type=str(LlmProviderTypes.anthropic),
        config={"anthropic_api_key": "other-key"},
    )
    matches = search_providers_by_api_key(ServiceProvider.llm, "sk-ant-secret-AbCdEf", match="exact")
    assert [p.pk for p in matches] == [anthropic_provider.pk]


@pytest.mark.django_db()
def test_search_empty_key_raises():
    with pytest.raises(ValueError, match="non-empty"):
        search_providers_by_api_key(ServiceProvider.llm, "", match="exact")


@pytest.mark.django_db()
def test_search_voice_provider(team_with_users):
    voice = VoiceProviderFactory(
        team=team_with_users,
        type=VoiceProviderType.aws,
        config={"aws_access_key_id": "AKIA-x", "aws_secret_access_key": "voice-secret"},
    )
    matches = search_providers_by_api_key(ServiceProvider.voice, "voice-secret", match="exact")
    assert voice in matches


@pytest.mark.django_db()
def test_search_messaging_provider(team_with_users):
    msg = MessagingProviderFactory(
        team=team_with_users,
        type=MessagingProviderType.twilio,
        config={"auth_token": "twilio-secret", "account_sid": "AC123"},
    )
    matches = search_providers_by_api_key(ServiceProvider.messaging, "twilio-secret", match="exact")
    assert msg in matches


@pytest.mark.django_db()
def test_search_trace_provider(team_with_users):
    trace = TraceProviderFactory(
        team=team_with_users,
        type=TraceProviderType.langfuse,
        config={"public_key": "pk", "secret_key": "trace-secret", "host": "https://example.com"},
    )
    matches = search_providers_by_api_key(ServiceProvider.tracing, "trace-secret", match="exact")
    assert trace in matches


@pytest.mark.django_db()
def test_search_invalid_match_mode_raises(anthropic_provider):
    with pytest.raises(ValueError, match="unknown match mode"):
        search_providers_by_api_key(ServiceProvider.llm, "key", match="fuzzy")  # type: ignore[arg-type]


@pytest.mark.django_db()
def test_usages_view_renders_version_tags(team_with_users, client):
    voice = VoiceProviderFactory(team=team_with_users)
    working = ExperimentFactory(team=team_with_users, voice_provider=voice)
    working.create_new_version()  # published v1; working is still a working version
    user = team_with_users.members.first()
    client.force_login(user)
    url = reverse(
        "service_providers:usages",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "voice", "pk": voice.pk},
    )
    response = client.get(url)

    body = response.content.decode()
    assert "working version" in body, "expected working-version badge"
    assert ("v1" in body) or ("published" in body), "expected a version badge for the published copy"

    # Both rows should point at the working version's URL; the v1 row appends #versions.
    working_url = working.get_absolute_url()
    assert f'href="{working_url}"' in body, "working version row should link directly to the working URL"
    assert f'href="{working_url}#versions"' in body, "older-version row should link with the #versions hash"


@pytest.mark.django_db()
def test_usages_view_renders(team_with_users, client, anthropic_provider):
    user = team_with_users.members.first()
    client.force_login(user)
    url = reverse(
        "service_providers:usages",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": anthropic_provider.pk},
    )
    response = client.get(url)
    assert response.status_code == 200
    assert b"Where is" in response.content
