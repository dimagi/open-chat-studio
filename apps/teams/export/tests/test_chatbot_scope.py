"""The row sets one chatbot selection resolves to. Each test builds two chatbots and asserts the
other one's rows stay out."""

import pytest
from django.db.models import QuerySet

from apps.annotations.models import CustomTaggedItem, UserComment
from apps.assessments.models import Score
from apps.events.models import EventAction
from apps.pipelines.models import PipelineChatMessages
from apps.teams.export import manifest
from apps.teams.export.chatbot_scope import CHATBOT_SCOPE_REGISTRY, build_scope, narrow_to_scope
from apps.utils.factories.annotations import CustomTaggedItemFactory, UserCommentFactory
from apps.utils.factories.assessments import ScoreFactory
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.custom_actions import CustomActionFactory, CustomActionOperationFactory
from apps.utils.factories.documents import CollectionFactory, CollectionFileFactory, DocumentSourceFactory
from apps.utils.factories.evaluations import EvaluationResultFactory
from apps.utils.factories.events import (
    ScheduledMessageFactory,
    ScheduledTriggerFactory,
    StaticTriggerFactory,
    TimeoutTriggerFactory,
)
from apps.utils.factories.experiment import (
    ChatMessageFactory,
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantDataFactory,
    ParticipantFactory,
    SourceMaterialFactory,
    SyntheticVoiceFactory,
)
from apps.utils.factories.files import FileFactory
from apps.utils.factories.human_annotations import AnnotationFactory
from apps.utils.factories.pipelines import (
    NodeFactory,
    PipelineChatHistoryFactory,
    PipelineChatMessagesFactory,
    PipelineFactory,
)
from apps.utils.factories.service_provider_factories import (
    AuthProviderFactory,
    EmbeddingProviderModelFactory,
    LlmProviderFactory,
    LlmProviderModelFactory,
    MessagingProviderFactory,
    TraceProviderFactory,
    VoiceProviderFactory,
)
from apps.utils.factories.team import TeamFactory

pytestmark = pytest.mark.django_db


def _ids(scope_set: list[int] | QuerySet) -> set[int]:
    """A scope set as ids, whether it is an id list or a one-column ``values()`` queryset."""
    if isinstance(scope_set, list):
        return set(scope_set)
    return {next(iter(row.values())) for row in scope_set}


def _pks(queryset) -> set[int]:
    return set(queryset.values_list("pk", flat=True))


def _select(team, **experiment_fields):
    chatbot = ExperimentFactory(team=team, **experiment_fields)
    team.exportable_experiments.add(chatbot)
    return chatbot


def _set(chatbot, **fields):
    for name, value in fields.items():
        setattr(chatbot, name, value)
    chatbot.save()
    return chatbot


def _node(chatbot, **fields):
    return NodeFactory(pipeline=chatbot.pipeline, **fields)


def _collection(chatbot, **fields):
    """A collection the chatbot's pipeline uses, with no providers unless ``fields`` sets them."""
    collection = CollectionFactory(
        team=chatbot.team, **{"llm_provider": None, "embedding_provider_model": None, **fields}
    )
    _node(chatbot, collection=collection)
    return collection


def _session(chatbot):
    return ExperimentSessionFactory(experiment=chatbot, team=chatbot.team)


def _attachment_file(chatbot):
    attachment = _session(chatbot).chat.attachments.create(tool_type="code_interpreter")
    file = FileFactory(team=chatbot.team)
    attachment.files.add(file)
    return file


def _collection_file(chatbot):
    return CollectionFileFactory(collection=_collection(chatbot), file=FileFactory(team=chatbot.team)).file


def _collection_index(chatbot):
    collection = CollectionFactory(team=chatbot.team, llm_provider=None, embedding_provider_model=None)
    _node(chatbot).collection_indexes.add(collection)
    return collection


def _custom_action(chatbot, **fields):
    action = CustomActionFactory(team=chatbot.team, **fields)
    CustomActionOperationFactory(custom_action=action, node=_node(chatbot))
    return action


def _document_source_auth(chatbot):
    provider = AuthProviderFactory(team=chatbot.team)
    DocumentSourceFactory(collection=_collection(chatbot), auth_provider=provider)
    return provider


def _scheduled_message(chatbot, participant):
    return ScheduledMessageFactory(
        team=chatbot.team,
        experiment=chatbot,
        participant=participant,
        action=None,
        custom_schedule_params={"name": "Test", "time_period": "days", "frequency": 1, "repetitions": 1},
    )


# Each case links one row to a chatbot and returns it. The test links one to the selected chatbot and
# one to another chatbot, so every path is checked for both inclusion and isolation.
SCOPE_SET_CASES = [
    # --- owned by the chatbot ---
    pytest.param("sessions", _session, id="session"),
    pytest.param("chats", lambda c: _session(c).chat, id="chat"),
    pytest.param("chat_messages", lambda c: ChatMessageFactory(chat=_session(c).chat), id="chat_message"),
    pytest.param("participants", lambda c: _session(c).participant, id="participant-session"),
    pytest.param(
        "participants",
        lambda c: ParticipantDataFactory(team=c.team, experiment=c).participant,
        id="participant-participant_data",
    ),
    pytest.param(
        "participants",
        lambda c: _scheduled_message(c, participant=ParticipantFactory(team=c.team)).participant,
        id="participant-scheduled_message",
    ),
    pytest.param("channel_ids", lambda c: ExperimentChannelFactory(team=c.team, experiment=c), id="channel"),
    # --- reached by what the chatbot uses ---
    pytest.param("pipeline_ids", lambda c: c.pipeline, id="pipeline"),
    pytest.param("node_ids", _node, id="node"),
    pytest.param("collection_ids", lambda c: _collection(c), id="collection-node"),
    pytest.param("collection_ids", _collection_index, id="collection-node_index"),
    pytest.param("files", _collection_file, id="file-collection"),
    pytest.param("files", _attachment_file, id="file-chat_attachment"),
    pytest.param(
        "files",
        lambda c: _set(c, synthetic_voice=SyntheticVoiceFactory(file=FileFactory(team=c.team))).synthetic_voice.file,
        id="file-synthetic_voice",
    ),
    pytest.param("custom_action_ids", _custom_action, id="custom_action"),
    pytest.param(
        "source_material_ids",
        lambda c: _node(c, source_material=SourceMaterialFactory(team=c.team)).source_material,
        id="source_material",
    ),
    pytest.param("consent_form_ids", lambda c: c.consent_form, id="consent_form"),
    pytest.param("synthetic_voice_ids", lambda c: c.synthetic_voice, id="synthetic_voice-chatbot"),
    pytest.param(
        "synthetic_voice_ids",
        lambda c: _node(c, synthetic_voice=SyntheticVoiceFactory()).synthetic_voice,
        id="synthetic_voice-node",
    ),
    pytest.param(
        "llm_provider_ids",
        lambda c: _node(c, llm_provider=LlmProviderFactory(team=c.team)).llm_provider,
        id="llm_provider-node",
    ),
    *[
        pytest.param(
            "llm_provider_ids",
            lambda c, field=field: getattr(_collection(c, **{field: LlmProviderFactory(team=c.team)}), field),
            id=f"llm_provider-collection_{field}",
        )
        for field in ("llm_provider", "contextualizer_llm_provider", "reranker_provider")
    ],
    pytest.param(
        "llm_provider_model_ids",
        lambda c: _node(c, llm_provider_model=LlmProviderModelFactory(team=c.team)).llm_provider_model,
        id="llm_provider_model-node",
    ),
    pytest.param(
        "llm_provider_model_ids",
        lambda c: (
            _collection(c, contextualizer_llm_model=LlmProviderModelFactory(team=c.team)).contextualizer_llm_model
        ),
        id="llm_provider_model-collection_contextualizer",
    ),
    pytest.param(
        "embedding_provider_model_ids",
        lambda c: (
            _collection(
                c, embedding_provider_model=EmbeddingProviderModelFactory(team=c.team, name=f"model-{c.id}")
            ).embedding_provider_model
        ),
        id="embedding_provider_model",
    ),
    pytest.param("voice_provider_ids", lambda c: c.voice_provider, id="voice_provider-chatbot"),
    pytest.param(
        "voice_provider_ids",
        lambda c: (
            _set(
                c, synthetic_voice=SyntheticVoiceFactory(voice_provider=VoiceProviderFactory(team=c.team))
            ).synthetic_voice.voice_provider
        ),
        id="voice_provider-synthetic_voice",
    ),
    pytest.param(
        "messaging_provider_ids",
        lambda c: (
            ExperimentChannelFactory(
                team=c.team, experiment=c, messaging_provider=MessagingProviderFactory(team=c.team)
            ).messaging_provider
        ),
        id="messaging_provider",
    ),
    pytest.param(
        "auth_provider_ids",
        lambda c: _custom_action(c, auth_provider=AuthProviderFactory(team=c.team)).auth_provider,
        id="auth_provider-custom_action",
    ),
    pytest.param("auth_provider_ids", _document_source_auth, id="auth_provider-document_source"),
    pytest.param(
        "trace_provider_ids",
        lambda c: _set(c, trace_provider=TraceProviderFactory(team=c.team)).trace_provider,
        id="trace_provider",
    ),
]


@pytest.mark.parametrize(("scope_set", "link"), SCOPE_SET_CASES)
def test_scope_set_follows_the_selected_chatbot(scope_set, link):
    team = TeamFactory()
    mine = link(_select(team))
    theirs = link(ExperimentFactory(team=team))

    ids = _ids(getattr(build_scope(team), scope_set))

    assert mine.id in ids
    assert theirs.id not in ids


def test_build_scope_is_none_without_a_selection():
    assert build_scope(TeamFactory()) is None


def test_build_scope_covers_the_selected_family():
    team = TeamFactory()
    working = ExperimentFactory(team=team)
    published = ExperimentFactory(team=team, working_version=working)
    other = ExperimentFactory(team=team)
    team.exportable_experiments.add(working)

    scope = build_scope(team)

    assert set(scope.experiment_ids) == {working.id, published.id}
    assert other.id not in scope.experiment_ids


def test_channels_include_the_teams_shared_ones():
    """Team-level channels (web, API, evaluations, widget) carry no experiment."""
    team = TeamFactory()
    _select(team)
    shared = ExperimentChannelFactory(team=team, experiment=None)

    assert shared.id in build_scope(team).channel_ids


@pytest.mark.parametrize(
    ("pipeline_id", "is_included"),
    [pytest.param("valid", True, id="valid"), pytest.param("not-a-number", False, id="malformed")],
)
def test_pipeline_closure_reads_pipelines_named_in_event_actions(pipeline_id, is_included):
    """A malformed legacy value is skipped rather than breaking the scope every request builds."""
    team = TeamFactory()
    chatbot = _select(team)
    started = PipelineFactory(team=team)
    trigger = StaticTriggerFactory(experiment=chatbot)
    trigger.action.action_type = "pipeline_start"
    trigger.action.params = {"pipeline_id": started.id if pipeline_id == "valid" else pipeline_id}
    trigger.action.save()

    ids = build_scope(team).pipeline_ids

    assert (started.id in ids) is is_included
    assert chatbot.pipeline_id in ids


def _published_pipeline(team, working):
    return ExperimentFactory(team=team, pipeline=PipelineFactory(team=team, working_version=working)).pipeline


def _published_file(team, working):
    chatbot = ExperimentFactory(team=team)
    file = FileFactory(team=team, working_version=working)
    CollectionFileFactory(collection=_collection(chatbot), file=file)
    return chatbot, file


@pytest.mark.parametrize("resource", [pytest.param("pipeline", id="pipeline"), pytest.param("file", id="file")])
def test_referenced_resources_include_their_working_versions(resource):
    """A published resource carries a self-referential working_version FK; exporting it without its
    working row leaves a link the target cannot resolve."""
    team = TeamFactory()
    if resource == "pipeline":
        working = PipelineFactory(team=team)
        mine = PipelineFactory(team=team, working_version=working)
        _select(team, pipeline=mine)
        theirs = _published_pipeline(team, working=working)
        ids = set(build_scope(team).pipeline_ids)
    else:
        working = FileFactory(team=team)
        chatbot, mine = _published_file(team, working=working)
        team.exportable_experiments.add(chatbot)
        _other, theirs = _published_file(team, working=working)
        ids = _ids(build_scope(team).files)

    assert {working.id, mine.id} <= ids
    assert theirs.id not in ids


TRIGGER_FACTORIES = [StaticTriggerFactory, TimeoutTriggerFactory, ScheduledTriggerFactory]


def _generic_target(kind):
    return {
        "chat": lambda c: _session(c).chat,
        "message": lambda c: ChatMessageFactory(chat=_session(c).chat),
        "session": _session,
    }[kind]


# Each case links one row of ``model`` to a chatbot and returns it.
OWNED_ROW_CASES = [
    *[
        pytest.param(
            EventAction,
            lambda c, factory=factory: factory(experiment=c).action,
            id=f"event_action-{factory._meta.model.__name__}",
        )
        for factory in TRIGGER_FACTORIES
    ],
    *[
        pytest.param(
            model,
            lambda c, factory=factory, kind=kind: factory(team=c.team, target=_generic_target(kind)(c)),
            id=f"{model.__name__}-{kind}",
        )
        for model, factory in ((CustomTaggedItem, CustomTaggedItemFactory), (UserComment, UserCommentFactory))
        for kind in ("chat", "message", "session")
    ],
    pytest.param(Score, lambda c: ScoreFactory(team=c.team, session=_session(c)), id="Score-session"),
    pytest.param(
        PipelineChatMessages,
        lambda c: PipelineChatMessagesFactory(chat_history=PipelineChatHistoryFactory(session=_session(c))),
        id="PipelineChatMessages",
    ),
]


@pytest.mark.parametrize(("model", "link"), OWNED_ROW_CASES)
def test_scope_rule_follows_the_selected_chatbot(model, link):
    """Generic-FK rows matter most: the import raises on one whose target was not synced."""
    team = TeamFactory()
    mine = link(_select(team))
    theirs = link(ExperimentFactory(team=team))

    pks = _pks(narrow_to_scope(model.objects.all(), model_label=model._meta.label_lower, scope=build_scope(team)))

    assert mine.id in pks
    assert theirs.id not in pks


@pytest.mark.parametrize(
    "provenance",
    [
        pytest.param(lambda team: {"automated_result": EvaluationResultFactory(team=team)}, id="evaluation_result"),
        pytest.param(lambda team: {"review": AnnotationFactory(team=team)}, id="human_annotation"),
    ],
)
def test_scores_from_excluded_models_are_left_out(provenance):
    """Their provenance FK points at a model the scoped export serves empty."""
    team = TeamFactory()
    score = ScoreFactory(team=team, session=_session(_select(team)), **provenance(team))

    scoped = narrow_to_scope(Score.objects.all(), model_label="assessments.score", scope=build_scope(team))

    assert score.id not in _pks(scoped)


def test_scope_registry_matches_the_manifest():
    """A manifest model without a rule would be served team-wide and drag in another chatbot's rows."""
    manifest_models = {e.model for e in manifest.MANIFEST_ENTRIES}
    assert set(CHATBOT_SCOPE_REGISTRY) == manifest_models, (
        f"Unclassified: {sorted(manifest_models - set(CHATBOT_SCOPE_REGISTRY))}; "
        f"not synced: {sorted(set(CHATBOT_SCOPE_REGISTRY) - manifest_models)}"
    )


def test_the_excluded_classes_are_exactly_the_brief():
    excluded = {label for label, rule in CHATBOT_SCOPE_REGISTRY.items() if rule.build_q is None}
    assert {label.split(".")[0] for label in excluded} == {"evaluations", "human_annotations", "analysis"}


def test_participants_are_reread_under_a_selection():
    """A participant who first talked to another chatbot joins the scope without their row changing."""
    assert CHATBOT_SCOPE_REGISTRY["experiments.participant"].reread_under_selection


def test_every_scope_rule_builds_a_runnable_queryset():
    """A misspelt lookup path raises FieldError only when the queryset runs. Running each rule once
    against an empty database is enough to catch that."""
    team = TeamFactory()
    team.exportable_experiments.add(ExperimentFactory(team=team))
    scope = build_scope(team)

    for entry in manifest.MANIFEST_ENTRIES:
        list(manifest.scoped_queryset(entry, team, scope)[:1])
