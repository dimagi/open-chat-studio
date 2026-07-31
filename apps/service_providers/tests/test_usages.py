import pytest
from django.db import connection
from django.template.loader import render_to_string
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.analysis.models import TranscriptAnalysis
from apps.events.models import EventActionType
from apps.service_providers.models import LlmProviderTypes, MessagingProviderType, TraceProviderType, VoiceProviderType
from apps.service_providers.usages import MAX_INLINE_VERSIONS, get_provider_usages, search_providers_by_api_key
from apps.service_providers.utils import ServiceProvider
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.documents import CollectionFactory, DocumentSourceFactory
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.factories.events import EventActionFactory, StaticTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory, SyntheticVoiceFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import (
    AuthProviderFactory,
    EmbeddingProviderModelFactory,
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
def test_other_categories_dedupe_and_sort(anthropic_provider):
    # TranscriptAnalysis has two FKs to LlmProvider (llm_provider +
    # translation_llm_provider). A row that sets both to the same provider
    # would otherwise surface twice in the bucket.
    team = anthropic_provider.team
    experiment = ExperimentFactory(team=team)
    user = team.members.first()
    common = {"team": team, "experiment": experiment, "created_by": user}
    TranscriptAnalysis.objects.create(
        **common, name="zebra", llm_provider=anthropic_provider, translation_llm_provider=anthropic_provider
    )
    TranscriptAnalysis.objects.create(**common, name="apple", llm_provider=anthropic_provider)
    TranscriptAnalysis.objects.create(**common, name="Mango", translation_llm_provider=anthropic_provider)

    usages = get_provider_usages(anthropic_provider)
    analyses = next(c for c in usages.categories if "Analysis" in c.label or "Analyses" in c.label)
    assert [a.name for a in analyses.items] == ["apple", "Mango", "zebra"], (
        "rows reachable via multiple FKs should appear once, sorted case-insensitively"
    )


@pytest.mark.django_db()
def test_chatbots_category_sorted_by_name(team_with_users):
    voice = VoiceProviderFactory(team=team_with_users)
    ExperimentFactory(team=team_with_users, voice_provider=voice, name="zebra")
    ExperimentFactory(team=team_with_users, voice_provider=voice, name="apple")
    ExperimentFactory(team=team_with_users, voice_provider=voice, name="Mango")

    usages = get_provider_usages(voice)

    chatbots = next(c for c in usages.categories if c.label == "Chatbots")
    assert [c.name for c in chatbots.items] == ["apple", "Mango", "zebra"], "chatbots should be sorted case-insensitive"


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
def test_document_sources_roll_up_to_collections(team_with_users, client):
    auth_provider = AuthProviderFactory(team=team_with_users)
    # Share the LlmProvider / EmbeddingProviderModel across collections so the
    # team-scoped unique constraints aren't violated by CollectionFactory.
    shared_llm = LlmProviderFactory(team=team_with_users)
    shared_embedding = EmbeddingProviderModelFactory(team=team_with_users)
    collection_a = CollectionFactory(
        team=team_with_users, name="Alpha", llm_provider=shared_llm, embedding_provider_model=shared_embedding
    )
    collection_b = CollectionFactory(
        team=team_with_users, name="Beta", llm_provider=shared_llm, embedding_provider_model=shared_embedding
    )
    DocumentSourceFactory(team=team_with_users, collection=collection_a, auth_provider=auth_provider)
    DocumentSourceFactory(team=team_with_users, collection=collection_a, auth_provider=auth_provider)
    DocumentSourceFactory(team=team_with_users, collection=collection_b, auth_provider=auth_provider)

    usages = get_provider_usages(auth_provider)

    collections = next(c for c in usages.categories if c.label == "Collections")
    assert {c.id for c in collections.items} == {collection_a.id, collection_b.id}
    assert len(collections.items) == 2, "duplicate collection should be deduped"

    user = team_with_users.members.first()
    client.force_login(user)
    response = client.get(
        reverse(
            "service_providers:usages_content",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "auth", "pk": auth_provider.pk},
        )
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert f'href="{collection_a.get_absolute_url()}"' in body
    assert "#versions" not in body, "the #versions anchor is reserved for chatbots"


@pytest.mark.django_db()
def test_voice_provider_excludes_synthetic_voices(team_with_users):
    voice = VoiceProviderFactory(team=team_with_users)
    chatbot = ExperimentFactory(team=team_with_users, voice_provider=voice)
    SyntheticVoiceFactory(voice_provider=voice)

    usages = get_provider_usages(voice)

    labels = {c.label for c in usages.categories}
    assert "Synthetic Voices" not in labels
    assert "Chatbots" in labels
    chatbots = next(c for c in usages.categories if c.label == "Chatbots")
    assert [item.id for item in chatbots.items] == [chatbot.id]


@pytest.mark.django_db()
def test_messaging_channels_roll_up_to_chatbots(team_with_users):
    messaging = MessagingProviderFactory(team=team_with_users)
    chatbot = ExperimentFactory(team=team_with_users)
    ExperimentChannelFactory(team=team_with_users, experiment=chatbot, messaging_provider=messaging)
    ExperimentChannelFactory(team=team_with_users, experiment=chatbot, messaging_provider=messaging)
    # Channel with no chatbot.
    ExperimentChannelFactory(team=team_with_users, experiment=None, messaging_provider=messaging)

    usages = get_provider_usages(messaging)

    labels = {c.label for c in usages.categories}
    assert labels == {"Chatbots", "Unlinked Channels"}

    chatbots_cat = next(c for c in usages.categories if c.label == "Chatbots")
    assert [item.id for item in chatbots_cat.items] == [chatbot.id]

    unlinked = next(c for c in usages.categories if c.label == "Unlinked Channels")
    assert len(unlinked.items) == 1


@pytest.mark.django_db()
def test_pipeline_chatbots_via_event_configuration(anthropic_provider):
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

    chatbots = next(c for c in usages.categories if c.label == "Chatbots")
    assert indirect_experiment.id in {item.id for item in chatbots.items}


@pytest.mark.django_db()
def test_pipelines_without_chatbots_appear_in_unlinked_category(anthropic_provider):
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

    unlinked = next(c for c in usages.categories if c.label == "Unlinked Pipelines")
    assert [p.id for p in unlinked.items] == [lonely_pipeline.id]


@pytest.mark.django_db()
def test_pipeline_chatbots_dedupe_direct_and_event_links(anthropic_provider):
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

    chatbots = next(c for c in usages.categories if c.label == "Chatbots")
    assert [item.id for item in chatbots.items] == [experiment.id], (
        "direct + event-driven references to the same chatbot should dedupe"
    )


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
def test_search_langfuse_by_public_key(team_with_users):
    """Langfuse exposes ``public_key`` as a non-secret identifier; it should still be searchable."""
    trace = TraceProviderFactory(
        team=team_with_users,
        type=TraceProviderType.langfuse,
        config={"public_key": "pk-lf-public-123", "secret_key": "trace-secret", "host": "https://example.com"},
    )
    matches = search_providers_by_api_key(ServiceProvider.tracing, "pk-lf-public-123", match="exact")
    assert trace in matches


@pytest.mark.django_db()
def test_search_aws_voice_by_access_key_id(team_with_users):
    voice = VoiceProviderFactory(
        team=team_with_users,
        type=VoiceProviderType.aws,
        config={
            "aws_access_key_id": "AKIA-PUBLIC-ID",
            "aws_secret_access_key": "voice-secret",
            "aws_region": "us-east-1",
        },
    )
    matches = search_providers_by_api_key(ServiceProvider.voice, "AKIA-PUBLIC-ID", match="exact")
    assert voice in matches


@pytest.mark.django_db()
def test_search_twilio_by_account_sid(team_with_users):
    msg = MessagingProviderFactory(
        team=team_with_users,
        type=MessagingProviderType.twilio,
        config={"auth_token": "twilio-secret", "account_sid": "AC-public-sid"},
    )
    matches = search_providers_by_api_key(ServiceProvider.messaging, "AC-public-sid", match="exact")
    assert msg in matches


@pytest.mark.django_db()
def test_search_invalid_match_mode_raises(anthropic_provider):
    with pytest.raises(ValueError, match="unknown match mode"):
        search_providers_by_api_key(ServiceProvider.llm, "key", match="fuzzy")  # type: ignore[arg-type]


@pytest.mark.django_db()
def test_usages_view_collapses_versions_into_one_row(team_with_users, client):
    voice = VoiceProviderFactory(team=team_with_users)
    working = ExperimentFactory(team=team_with_users, voice_provider=voice, name="Interview Bot")
    version = working.create_new_version()  # published v1; working is still a working version
    user = team_with_users.members.first()
    client.force_login(user)
    url = reverse(
        "service_providers:usages_content",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "voice", "pk": voice.pk},
    )
    response = client.get(url)

    body = response.content.decode()
    assert body.count(">Interview Bot</a>") == 1, "the family should occupy a single row"
    assert "Chatbots (1)" in body, "the category count should count families, not versions"
    assert "working version" in body, "expected working-version badge"
    assert "published v1" in body, "expected a version badge for the published copy"

    # The row name links to the working version; the v1 badge deep-links to that version's row.
    assert f'href="{working.get_absolute_url()}"' in body
    assert f'href="{version.get_absolute_url()}"' in body
    assert "?version_id=1#versions" in version.get_absolute_url(), "sanity: version URLs are version-aware"


@pytest.mark.django_db()
def test_versions_collapse_into_one_group(team_with_users):
    voice = VoiceProviderFactory(team=team_with_users)
    working = ExperimentFactory(team=team_with_users, voice_provider=voice)
    v1 = working.create_new_version()
    v2 = working.create_new_version()

    usages = get_provider_usages(voice)

    chatbots = next(c for c in usages.categories if c.label == "Chatbots")
    assert {item.id for item in chatbots.items} == {working.id, v1.id, v2.id}, "sanity: all three reference the voice"
    (group,) = chatbots.groups
    assert group.head.id == working.id
    assert [m.id for m in group.versions] == [working.id, v1.id, v2.id], "working version first, then ascending"
    assert usages.total == 1, "the total counts families, matching the rows shown"


@pytest.mark.django_db()
def test_group_head_is_the_working_version_even_when_only_a_snapshot_references_the_provider(team_with_users):
    voice = VoiceProviderFactory(team=team_with_users)
    working = ExperimentFactory(team=team_with_users, voice_provider=voice, name="Interview Bot")
    v1 = working.create_new_version()
    working.voice_provider_id = None
    working.save()

    usages = get_provider_usages(voice)

    (group,) = next(c for c in usages.categories if c.label == "Chatbots").groups
    assert [m.id for m in group.versions] == [v1.id]
    assert group.head.id == working.id, "the row must still link to the family's editable working version"
    assert group.name == "Interview Bot"


@pytest.mark.django_db()
def test_versions_past_the_inline_limit_collapse_behind_a_toggle(team_with_users, client):
    voice = VoiceProviderFactory(team=team_with_users)
    working = ExperimentFactory(team=team_with_users, voice_provider=voice)
    extra = 2
    for _ in range(MAX_INLINE_VERSIONS - 1 + extra):  # working version occupies one inline slot
        working.create_new_version()

    user = team_with_users.members.first()
    client.force_login(user)
    response = client.get(
        reverse(
            "service_providers:usages_content",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "voice", "pk": voice.pk},
        )
    )

    body = response.content.decode()
    assert f"+{extra} more" in body
    assert "Chatbots (1)" in body


@pytest.mark.django_db()
def test_versions_of_models_without_version_urls_are_not_linked(team_with_users, client, anthropic_provider):
    """Only chatbots deep-link to a version; other snapshots have no page to link to."""
    assistant = OpenAiAssistantFactory(team=team_with_users, llm_provider=anthropic_provider)
    # Built directly rather than via ``create_new_version``, which would push to OpenAI.
    version = OpenAiAssistantFactory(
        team=team_with_users,
        llm_provider=anthropic_provider,
        working_version=assistant,
        version_number=1,
    )

    user = team_with_users.members.first()
    client.force_login(user)
    response = client.get(
        reverse(
            "service_providers:usages_content",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": anthropic_provider.pk},
        )
    )

    body = response.content.decode()
    assert "v1" in body, "the version is still reported"
    assert f'href="{version.get_absolute_url()}"' not in body, "a snapshot's own url is not a usable destination"
    assert f'href="{assistant.get_absolute_url()}"' in body, "the row links to the working version"


def _add_provider_usage(provider, count: int) -> None:
    """Attach ``count`` more references: a pipeline node (with its chatbot) and an evaluator."""
    for _ in range(count):
        pipeline = PipelineFactory(team=provider.team)
        NodeFactory(pipeline=pipeline, type="LLMResponseWithPrompt", params={"llm_provider_id": provider.id})
        ExperimentFactory(team=provider.team, pipeline=pipeline)
        EvaluatorFactory(team=provider.team, llm_provider=provider)


@pytest.mark.django_db()
def test_evaluators_referencing_the_provider_are_reported(anthropic_provider):
    evaluator = EvaluatorFactory(team=anthropic_provider.team, name="Sentiment", llm_provider=anthropic_provider)

    usages = get_provider_usages(anthropic_provider)

    items_by_label = {c.label: [obj.id for obj in c.items] for c in usages.categories}
    assert items_by_label == {"Evaluators": [evaluator.id]}


@pytest.mark.django_db()
def test_evaluators_referencing_a_different_provider_are_not_reported(anthropic_provider):
    other_provider = LlmProviderFactory(team=anthropic_provider.team)
    EvaluatorFactory(team=anthropic_provider.team, llm_provider=other_provider)

    assert get_provider_usages(anthropic_provider).is_empty()


@pytest.mark.django_db()
def test_archived_pipelines_are_still_reported(anthropic_provider):
    """A provider referenced only by an archived pipeline must not look unreferenced."""
    team = anthropic_provider.team
    pipeline = PipelineFactory(team=team, name="Archived", is_archived=True)
    NodeFactory(pipeline=pipeline, type="LLMResponseWithPrompt", params={"llm_provider_id": anthropic_provider.id})

    usages = get_provider_usages(anthropic_provider)

    items_by_label = {c.label: [obj.id for obj in c.items] for c in usages.categories}
    assert items_by_label == {"Unlinked Pipelines": [pipeline.id]}


@pytest.mark.django_db()
def test_resolving_usages_does_not_scale_with_reference_count(anthropic_provider):
    """N+1 guard: query count must be flat in the number of referencing objects."""
    _add_provider_usage(anthropic_provider, 1)
    with CaptureQueriesContext(connection) as few:
        usages_few = get_provider_usages(anthropic_provider)

    _add_provider_usage(anthropic_provider, 4)
    with CaptureQueriesContext(connection) as many:
        usages_many = get_provider_usages(anthropic_provider)

    assert usages_few.total == 2, "sanity: one chatbot + one evaluator"
    assert usages_many.total == 10, "sanity: five chatbots + five evaluators"
    assert len(many.captured_queries) == len(few.captured_queries), (
        f"query count grew from {len(few.captured_queries)} to {len(many.captured_queries)} "
        f"when references went from 2 to 10"
    )


@pytest.mark.django_db()
def test_rendering_usages_does_not_scale_with_version_count(team_with_users):
    """N+1 guard: the item partial resolves working versions without a query per row."""
    voice = VoiceProviderFactory(team=team_with_users)

    def render_for(provider):
        return render_to_string(
            "service_providers/components/usages_content.html",
            {"provider": provider, "usages": get_provider_usages(provider)},
        )

    working = ExperimentFactory(team=team_with_users, voice_provider=voice)
    working.create_new_version()
    render_for(voice)  # warm any process-level caches (e.g. team slug lookups)
    with CaptureQueriesContext(connection) as few:
        render_for(voice)

    for _ in range(3):
        extra = ExperimentFactory(team=team_with_users, voice_provider=voice)
        extra.create_new_version()
    with CaptureQueriesContext(connection) as many:
        render_for(voice)

    assert len(many.captured_queries) == len(few.captured_queries), (
        f"query count grew from {len(few.captured_queries)} to {len(many.captured_queries)} "
        f"when versioned chatbot references went from 2 to 8"
    )


@pytest.mark.django_db()
def test_usages_view_renders_shell_with_loading_state(team_with_users, client, anthropic_provider):
    """The shell must not resolve usages itself; it defers to the content view."""
    user = team_with_users.members.first()
    client.force_login(user)
    url = reverse(
        "service_providers:usages",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": anthropic_provider.pk},
    )
    response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    assert "Where is" in body
    assert "loading-spinner" in body
    content_url = reverse(
        "service_providers:usages_content",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": anthropic_provider.pk},
    )
    assert f'hx-get="{content_url}"' in body


@pytest.mark.django_db()
def test_usages_content_view_renders(team_with_users, client, anthropic_provider):
    user = team_with_users.members.first()
    client.force_login(user)
    url = reverse(
        "service_providers:usages_content",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": anthropic_provider.pk},
    )
    response = client.get(url)
    assert response.status_code == 200
    assert b"not currently referenced" in response.content
