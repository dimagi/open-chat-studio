from datetime import UTC, datetime

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.db import models
from django.db.models.signals import post_save

from apps.annotations.models import Tag, UserComment
from apps.api.export.serializers import build_resource_serializer
from apps.assessments.models import Score
from apps.channels.models import ExperimentChannel
from apps.chat.models import Chat, ChatMessage
from apps.experiments.models import ConsentForm, ExperimentSession
from apps.files.models import File
from apps.human_annotations.models import Annotation, AnnotationQueue
from apps.pipelines.models import Node, Pipeline
from apps.service_providers.models import LlmProvider
from apps.teams.export import seal as seal_mod
from apps.teams.export.client import FileContentNotFound
from apps.teams.export.importer import Importer, MissingGlobalRow, UnresolvedForeignKey, mute_signals
from apps.teams.export.manifest import GLOBAL_CONFIG, MANIFEST_ENTRIES, entry_model, generic_fk_fields
from apps.teams.export.translation import FKTranslationStore
from apps.teams.models import Membership, Team
from apps.users.models import CustomUser
from apps.utils.factories.analysis import AnalysisQueryFactory, TranscriptAnalysisFactory
from apps.utils.factories.annotations import CustomTaggedItemFactory, TagFactory, UserCommentFactory
from apps.utils.factories.assessments import ScoreFactory
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.cost_tracking import PricingRuleFactory, UsageRecordFactory
from apps.utils.factories.custom_actions import CustomActionFactory, CustomActionOperationFactory
from apps.utils.factories.documents import CollectionFactory, CollectionFileFactory, DocumentSourceFactory
from apps.utils.factories.evaluations import (
    AppliedTagFactory,
    DatasetAutoPopulationRuleFactory,
    EvaluationConfigFactory,
    EvaluationDatasetFactory,
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunAggregateFactory,
    EvaluationRunFactory,
    EvaluationTagFactory,
    EvaluatorFactory,
    EvaluatorTagRuleFactory,
)
from apps.utils.factories.events import (
    EventActionFactory,
    ScheduledMessageFactory,
    StaticTriggerFactory,
    TimeoutTriggerFactory,
)
from apps.utils.factories.experiment import (
    ChatAttachmentFactory,
    ChatFactory,
    ChatMessageFactory,
    ConsentFormFactory,
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantDataFactory,
    ParticipantFactory,
    SourceMaterialFactory,
    SyntheticVoiceFactory,
)
from apps.utils.factories.files import FileChunkEmbeddingFactory, FileFactory
from apps.utils.factories.human_annotations import (
    AnnotationFactory,
    AnnotationItemFactory,
    AnnotationQueueAggregateFactory,
    AnnotationQueueFactory,
)
from apps.utils.factories.notifications import (
    EventTypeFactory,
    EventUserFactory,
    NotificationEventFactory,
    UserNotificationPreferencesFactory,
)
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
from apps.utils.factories.traces import TraceFactory
from apps.utils.factories.user import UserFactory


def _user_row(source_id, username, **extra):
    return {
        "id": source_id,
        "username": username,
        "email": username,
        "first_name": "",
        "last_name": "",
        "is_active": True,
        "is_staff": False,
        "is_superuser": False,
        "date_joined": PAST,
        "last_login": None,
        "groups": [],
        **extra,
    }


pytestmark = pytest.mark.django_db

PAST = "2020-01-02T03:04:05+00:00"


@pytest.fixture()
def store(tmp_path):
    return FKTranslationStore(tmp_path / "team.sqlite")


@pytest.fixture()
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return seal_mod.load_public_key(public_pem), private


def _team_row(source_id=9001, slug="imported-team-xyz"):
    return {
        "id": source_id,
        "name": "Imported",
        "slug": slug,
        "feature_flags": [],
        "created_at": PAST,
        "updated_at": PAST,
    }


def test_import_team_creates_row_records_translation_and_restores_timestamps(store):
    """A team row is created, its source->target id recorded, and source timestamps kept."""
    Importer(store).import_rows("teams.team", [_team_row()])

    target_pk = store.get_target("teams.team", 9001)
    team = Team.objects.get(pk=target_pk)
    assert team.slug == "imported-team-xyz"
    assert team.created_at == datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_import_resolves_fk_to_target_pk_and_unseals_secret(store, keypair):
    """A provider's team FK is rewritten to the target team and its sealed config decrypted."""
    public_key, private_key = keypair
    importer = Importer(store, private_key=private_key)
    importer.import_rows("teams.team", [_team_row()])
    target_team = store.get_target("teams.team", 9001)

    provider_row = {
        "id": 5,
        "name": "OpenAI",
        "type": "openai",
        "config": seal_mod.seal({"api_key": "sk-x"}, public_key),
        "created_at": PAST,
        "updated_at": PAST,
    }
    importer.import_rows("service_providers.llmprovider", [provider_row])

    provider = LlmProvider.objects.get(pk=store.get_target("service_providers.llmprovider", 5))
    assert provider.team_id == target_team  # assigned to the synced team, not read from the row
    assert provider.config == {"api_key": "sk-x"}


def test_rerun_does_not_duplicate(store):
    """Re-importing the same row maps to the existing target instead of creating a duplicate."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    first_pk = store.get_target("teams.team", 9001)
    importer.import_rows("teams.team", [_team_row()])
    assert store.get_target("teams.team", 9001) == first_pk
    assert Team.objects.filter(slug="imported-team-xyz").count() == 1


def test_reimporting_soft_deleted_channel_is_idempotent(store):
    """A soft-deleted channel is exported (deleted=True). Re-importing it must map to the existing
    target row, not create a duplicate -- so the existence check has to see deleted rows, which the
    default manager filters out. A duplicate would also violate the channel's unique external_id."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])  # captures the target team

    channel_row = {
        "id": 555,
        "name": "Deleted TG",
        "experiment": None,
        "deleted": True,
        "extra_data": {"bot_token": "x"},
        "external_id": "31cfe1e9-4b5a-4ffd-8d17-e8cb161d3921",
        "platform": "telegram",
        "messaging_provider": None,
        "widget_version": None,
        "widget_version_updated_at": None,
        "created_at": PAST,
        "updated_at": PAST,
    }
    importer.import_rows("bot_channels.experimentchannel", [dict(channel_row)])
    first_pk = store.get_target("bot_channels.experimentchannel", 555)
    importer.import_rows("bot_channels.experimentchannel", [dict(channel_row)])

    assert store.get_target("bot_channels.experimentchannel", 555) == first_pk
    channels = ExperimentChannel.objects.get_unfiltered_queryset()
    assert channels.filter(external_id=channel_row["external_id"]).count() == 1


# A natural-key sample per GLOBAL_CONFIG model, used to build a matching pair of rows. Keyed by
# model label so a newly added global model fails the test below until it gets a sample here.
_GLOBAL_MATCH_SAMPLES = {
    "service_providers.llmprovidermodel": {"type": "openai", "name": "shared", "max_token_limit": 8192},
    "service_providers.embeddingprovidermodel": {"type": "openai", "name": "shared"},
    "experiments.syntheticvoice": {
        "name": "shared",
        "language_code": "en",
        "language": "English",
        "gender": "male",
        "neural": True,
        "service": "AWS",
    },
}


def _scoping_owner(null_field):
    """A real owner for the team-scoped twin -- the value whose absence (null) marks a row global."""
    if null_field == "team":
        return TeamFactory()
    if null_field == "voice_provider":
        return VoiceProviderFactory()
    raise AssertionError(f"no scoping-owner factory for global null_field {null_field!r}")


@pytest.mark.parametrize("model_label", list(GLOBAL_CONFIG))
def test_global_row_matches_the_single_shared_record_not_a_team_scoped_twin(store, model_label):
    """Every GLOBAL_CONFIG model resolves a global row to the one shared (teamless) target by natural
    key. A team-scoped row sharing that natural key must be ignored, and nothing is recreated."""
    spec = GLOBAL_CONFIG[model_label]
    natural_key = _GLOBAL_MATCH_SAMPLES.get(model_label)
    assert natural_key is not None, f"add a natural-key sample for new GLOBAL_CONFIG entry {model_label!r}"
    model = entry_model(model_label)

    model.objects.create(**{spec.null_field: _scoping_owner(spec.null_field)}, **natural_key)  # team-scoped twin
    global_row = model.objects.create(**{spec.null_field: None}, **natural_key)
    count_before = model.objects.count()

    Importer(store).import_rows(model_label, [{"id": 77, "is_global": True, **natural_key}])

    assert model.objects.filter(**{f"{spec.null_field}__isnull": True}, **natural_key).count() == 1
    assert store.get_target(model_label, 77) == global_row.pk
    assert model.objects.count() == count_before  # matched, not recreated


def test_missing_global_row_raises_with_its_natural_key(store):
    """A global row the source serves but the target lacks must fail loudly (naming the missing row),
    not silently null the references that point at it or abort obscurely deep in FK resolution."""
    row = {
        "id": 77,
        "is_global": True,
        "type": "openai",
        "name": "model-not-on-target",
        "max_token_limit": 8192,
        "deprecated": False,
        "created_at": PAST,
        "updated_at": PAST,
    }

    with pytest.raises(MissingGlobalRow, match="model-not-on-target"):
        Importer(store).import_rows("service_providers.llmprovidermodel", [row])

    assert store.get_target("service_providers.llmprovidermodel", 77) is None


def test_node_params_and_fk_columns_are_remapped(store):
    """Resource ids in both FK columns and node params are translated to their target pks."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)
    importer.import_rows(
        "service_providers.llmprovider",
        [{"id": 7, "name": "P", "type": "openai", "config": {}, "created_at": PAST, "updated_at": PAST}],
    )
    provider_pk = store.get_target("service_providers.llmprovider", 7)
    importer.import_rows(
        "pipelines.pipeline",
        [
            {
                "id": 100,
                "name": "Flow",
                "data": {"nodes": [], "edges": []},
                "version_number": 1,
                "is_archived": False,
                "working_version": None,
                "created_at": PAST,
                "updated_at": PAST,
            }
        ],
    )
    pipeline_pk = store.get_target("pipelines.pipeline", 100)
    node_row = {
        "id": 200,
        "flow_id": "n1",
        "type": "LLMResponseWithPrompt",
        "label": "",
        "params": {"name": "n1", "llm_provider_id": 7},
        "llm_provider": 7,
        "pipeline": 100,
        "working_version": None,
        "is_archived": False,
        "created_at": PAST,
        "updated_at": PAST,
    }
    importer.import_rows("pipelines.node", [node_row])

    node = Node.objects.get(pk=store.get_target("pipelines.node", 200))
    assert node.pipeline_id == pipeline_pk
    assert node.llm_provider_id == provider_pk
    assert node.params["llm_provider_id"] == provider_pk
    assert Pipeline.objects.get(pk=pipeline_pk).team_id == team_pk


def test_importing_user_creates_team_membership_with_role_groups(store):
    """A user row carries the user's role in the synced team; importing it creates the membership
    and links the role groups (matched by name) -- no separate membership resource needed."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)
    Group.objects.get_or_create(name="Sync Role X")

    importer.import_rows("users.customuser", [_user_row(50, "u@example.com", groups=["Sync Role X"])])

    user = CustomUser.objects.get(pk=store.get_target("users.customuser", 50))
    membership = Membership.objects.get(team_id=team_pk, user=user)
    assert list(membership.groups.values_list("name", flat=True)) == ["Sync Role X"]


def test_reimporting_user_does_not_duplicate_membership(store):
    """Re-running maps the user to the same membership rather than creating a second one."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)

    importer.import_rows("users.customuser", [_user_row(50, "u@example.com")])
    importer.import_rows("users.customuser", [_user_row(50, "u@example.com")])

    user = CustomUser.objects.get(pk=store.get_target("users.customuser", 50))
    assert Membership.objects.filter(team_id=team_pk, user=user).count() == 1


def _chat_row(source_id=77, translated_languages=None, metadata=None):
    return {
        "id": source_id,
        "name": "Imported Chat",
        "translated_languages": translated_languages if translated_languages is not None else ["en"],
        "metadata": metadata if metadata is not None else {},
        "created_at": PAST,
        "updated_at": PAST,
    }


def _session_row(source_id=33, experiment_src=11, participant_src=22, chat_src=77, **extra):
    return {
        "id": source_id,
        "experiment": experiment_src,
        "participant": participant_src,
        "experiment_channel": None,
        "chat": chat_src,
        "created_at": PAST,
        "updated_at": PAST,
        **extra,
    }


def _seed_session_fks(store, team_pk):
    """Stand in a target experiment and participant for a session row to reference, recording their
    source->target translations so the session's FKs resolve."""
    team = Team.objects.get(pk=team_pk)
    experiment = ExperimentFactory(team=team)
    participant = ParticipantFactory(team=team)
    store.record("experiments.experiment", 11, experiment.id)
    store.record("experiments.participant", 22, participant.id)


def test_importing_chat_creates_it_in_the_synced_team(store):
    """Chat is its own synced resource now -- importing a chat row creates it in the synced team and
    records its id translation."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)

    importer.import_rows("chat.chat", [_chat_row(translated_languages=["en"], metadata={"k": "v"})])

    chat = Chat.objects.get(pk=store.get_target("chat.chat", 77))
    assert chat.team_id == team_pk
    assert chat.translated_languages == ["en"]
    assert chat.metadata == {"k": "v"}


def test_session_chat_fk_resolves_to_the_imported_chat(store):
    """A session references its chat by FK; with the chat imported first, the session's chat_id
    resolves to the target chat."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)
    _seed_session_fks(store, team_pk)
    importer.import_rows("chat.chat", [_chat_row(source_id=77)])

    importer.import_rows("experiments.experimentsession", [_session_row(chat_src=77)])

    session = ExperimentSession.objects.get(pk=store.get_target("experiments.experimentsession", 33))
    assert session.chat_id == store.get_target("chat.chat", 77)


def _chat_message_row(source_id, chat_src, message_type, created_at):
    return {
        "id": source_id,
        "chat": chat_src,
        "message_type": message_type,
        "content": "test message",
        "summary": None,
        "translations": {},
        "metadata": {},
        "created_at": created_at,
        "updated_at": created_at,
    }


def test_muted_import_keeps_session_activity_timestamps_from_source(store):
    """Within mute_signals, importing chat messages no longer fires the ChatMessage post_save handler
    that would otherwise stamp the session's activity timestamps to the import time -- so the session
    keeps the first/last_activity_at copied straight from its source row."""
    source_first = datetime(2020, 5, 1, tzinfo=UTC)
    source_last = datetime(2022, 6, 15, tzinfo=UTC)

    importer = Importer(store)
    with mute_signals():
        importer.import_rows("teams.team", [_team_row()])
        team_pk = store.get_target("teams.team", 9001)
        _seed_session_fks(store, team_pk)
        importer.import_rows("chat.chat", [_chat_row(source_id=77)])
        importer.import_rows(
            "experiments.experimentsession",
            [
                _session_row(
                    source_id=33,
                    chat_src=77,
                    first_activity_at=source_first.isoformat(),
                    last_activity_at=source_last.isoformat(),
                )
            ],
        )
        importer.import_rows("chat.chatmessage", [_chat_message_row(101, 77, "human", PAST)])

    session = ExperimentSession.objects.get(pk=store.get_target("experiments.experimentsession", 33))
    assert session.first_activity_at == source_first
    assert session.last_activity_at == source_last


def test_mute_signals_suppresses_then_restores_receivers(store):
    """mute_signals clears model-signal receivers inside the block and restores them afterwards, so
    normal app behaviour resumes once the import is done."""
    fired = []

    def _record(sender, instance, **kwargs):
        fired.append(instance)

    post_save.connect(_record, sender=Team, dispatch_uid="test-mute-signals")
    try:
        with mute_signals():
            TeamFactory()
        assert fired == []  # suppressed inside the block

        TeamFactory()
        assert len(fired) == 1  # reconnected after the block
    finally:
        post_save.disconnect(_record, sender=Team, dispatch_uid="test-mute-signals")


def test_default_consent_form_maps_to_auto_created_default(store):
    """The source's default consent form maps onto the team's auto-created default, not a second one."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)
    auto_default = ConsentForm.objects.get(team_id=team_pk, is_default=True)

    row = {
        "id": 300,
        "name": "Imported Consent",
        "consent_text": "Source consent text",
        "is_default": True,
        "capture_identifier": True,
        "identifier_label": "Email Address",
        "identifier_type": "email",
        "confirmation_text": "ok",
        "is_archived": False,
        "working_version": None,
        "created_at": PAST,
        "updated_at": PAST,
    }
    importer.import_rows("experiments.consentform", [row])

    assert store.get_target("experiments.consentform", 300) == auto_default.pk
    auto_default.refresh_from_db()
    assert auto_default.consent_text == "Source consent text"
    assert ConsentForm.objects.filter(team_id=team_pk, is_default=True).count() == 1


def test_tag_slug_taken_by_another_team_gets_a_fresh_slug(store):
    """Tag slugs are unique across the whole server (taggit) while tags are team-scoped, so a source
    tag's slug may already be taken by another team on the target. The slug is regenerated on import
    rather than copied, so the create can't collide -- even when an older source still sends it."""
    TagFactory(name="urgent")  # another team already holds slug "urgent"
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])

    row = {
        "id": 55,
        "name": "urgent",
        "slug": "urgent",
        "is_system_tag": False,
        "category": "",
        "created_by": None,
        "created_at": PAST,
        "updated_at": PAST,
    }
    importer.import_rows("annotations.tag", [row])

    tag = Tag.objects.get(pk=store.get_target("annotations.tag", 55))
    assert tag.name == "urgent"
    assert tag.slug
    assert tag.slug != "urgent"


def test_existing_user_matched_by_username_is_not_overwritten_or_emailed(store):
    """A user already on the target is mapped by username, left unchanged, and gets no reset email --
    but is still given a membership in the synced team so they can access the imported data."""
    existing = CustomUser.objects.create(username="op@example.com", email="op@example.com", first_name="Original")
    created_users = []
    importer = Importer(store, on_user_created=created_users.append)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)

    importer.import_rows("users.customuser", [_user_row(50, "op@example.com", first_name="Source")])

    assert store.get_target("users.customuser", 50) == existing.pk
    existing.refresh_from_db()
    assert existing.first_name == "Original"  # an existing account is mapped, never overwritten
    assert created_users == []  # not newly added -> no reset email
    assert Membership.objects.filter(team_id=team_pk, user=existing).exists()  # joined to the synced team


def test_new_user_triggers_on_user_created(store):
    """A newly created user fires the on_user_created callback (e.g. the reset email)."""
    created_users = []
    importer = Importer(store, on_user_created=created_users.append)
    importer.import_rows("teams.team", [_team_row()])

    importer.import_rows("users.customuser", [_user_row(51, "fresh@example.com")])

    assert [u.username for u in created_users] == ["fresh@example.com"]


def _fail_when_filling_checkpoint(store):
    """Wrap store.record so it raises on the finalizing write (target_key set), simulating a crash
    after the row is inserted but before its checkpoint is filled."""
    real_record = store.record

    def record(content_type, source_key, target_key=None):
        if target_key is not None:
            raise RuntimeError("interrupted before checkpoint was filled")
        return real_record(content_type, source_key, target_key)

    return record


def test_interrupted_finalize_rolls_back_the_created_row(store, monkeypatch):
    """A crash after the INSERT but before the checkpoint is filled must roll the row back; an
    orphaned row with no checkpoint is exactly what a rerun would duplicate."""
    monkeypatch.setattr(store, "record", _fail_when_filling_checkpoint(store))

    with pytest.raises(RuntimeError):
        Importer(store).import_rows("teams.team", [_team_row()])

    assert Team.objects.filter(slug="imported-team-xyz").count() == 0


def test_rerun_after_interruption_creates_exactly_one_row(store, monkeypatch):
    """After an interrupted run rolls its row back, a clean rerun creates exactly one -- no duplicate."""
    monkeypatch.setattr(store, "record", _fail_when_filling_checkpoint(store))
    with pytest.raises(RuntimeError):
        Importer(store).import_rows("teams.team", [_team_row()])
    monkeypatch.undo()

    Importer(store).import_rows("teams.team", [_team_row()])

    assert Team.objects.filter(slug="imported-team-xyz").count() == 1


def _comment_row(source_id, user_src, content_type="chat.chatmessage", object_id=88):
    return {
        "id": source_id,
        "user": user_src,
        "comment": "nice",
        "content_type": content_type,
        "object_id": object_id,
        "created_at": PAST,
        "updated_at": PAST,
    }


def test_generic_fk_resolves_content_type_and_object_id(store):
    """A generic FK names its own target model; the importer maps the content type by name and
    translates the object id through the store, so the new row points at the target's row."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    team_pk = store.get_target("teams.team", 9001)

    user = UserFactory()
    store.record("users.customuser", 60, user.id)
    message = ChatMessageFactory(chat=ChatFactory())
    store.record("chat.chatmessage", 88, message.id)

    importer.import_rows("annotations.usercomment", [_comment_row(source_id=500, user_src=60)])

    comment = UserComment.objects.get(pk=store.get_target("annotations.usercomment", 500))
    assert comment.content_type == ContentType.objects.get_for_model(ChatMessage)
    assert comment.object_id == message.id
    assert comment.user_id == user.id
    assert comment.team_id == team_pk


def test_rerun_does_not_duplicate_generic_fk_row(store):
    """Re-importing a generic-FK row maps it to the existing target via the translation table rather
    than creating a second row."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    store.record("users.customuser", 60, UserFactory().id)
    store.record("chat.chatmessage", 88, ChatMessageFactory(chat=ChatFactory()).id)

    importer.import_rows("annotations.usercomment", [_comment_row(source_id=500, user_src=60)])
    first = store.get_target("annotations.usercomment", 500)
    importer.import_rows("annotations.usercomment", [_comment_row(source_id=500, user_src=60)])

    assert store.get_target("annotations.usercomment", 500) == first
    assert UserComment.objects.count() == 1


def test_generic_fk_with_untranslated_target_raises(store):
    """If a generic FK's target was never imported, resolution must fail loudly rather than silently
    nulling the relation -- matching how regular FKs behave."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    store.record("users.customuser", 60, UserFactory().id)
    # chat.chatmessage 88 (the comment's object_id) is deliberately never recorded.

    with pytest.raises(UnresolvedForeignKey):
        importer.import_rows("annotations.usercomment", [_comment_row(source_id=500, user_src=60)])


def _annotation_queue_row(source_id, assignees, name="Queue"):
    return {
        "id": source_id,
        "name": name,
        "assignees": assignees,
        "created_by": None,
        "created_at": PAST,
        "updated_at": PAST,
    }


def test_m2m_members_translate_to_target_pks(store):
    """An m2m field's members are remapped from source pks to their imported target pks, so the link
    is recreated against the target's rows rather than the (meaningless) source ids."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    user_a, user_b = UserFactory(), UserFactory()
    store.record("users.customuser", 71, user_a.id)
    store.record("users.customuser", 72, user_b.id)

    importer.import_rows("human_annotations.annotationqueue", [_annotation_queue_row(600, assignees=[71, 72])])

    queue = AnnotationQueue.objects.get(pk=store.get_target("human_annotations.annotationqueue", 600))
    assert set(queue.assignees.values_list("id", flat=True)) == {user_a.id, user_b.id}


def test_m2m_member_with_untranslated_target_raises(store):
    """A populated m2m member whose (synced) target was never imported fails loudly rather than
    silently dropping the link -- matching scalar FK resolution."""
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    store.record("users.customuser", 71, UserFactory().id)
    # source user 72 is deliberately never recorded.

    with pytest.raises(UnresolvedForeignKey):
        importer.import_rows("human_annotations.annotationqueue", [_annotation_queue_row(600, assignees=[71, 72])])


def test_importing_a_score_maps_onto_the_annotation_side_effect_row(store):
    """Regression for the sync_team UniqueViolation on ``score_unique_per_review_field``.

    Importing a submitted Annotation runs ``Annotation.save()``, which calls
    ``write_scores_from_annotation`` and writes Score rows on the target as a side effect. Because
    that write lives in a ``save()`` override -- not a signal -- ``mute_signals`` doesn't stop it. The
    manifest then imports the source's own Score rows (assessments.score comes after annotations), and
    each one used to collide on ``(review, name)`` with the score the annotation save just wrote. The
    score is now matched onto that existing row instead of inserting a duplicate.
    """
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])
    target_team = Team.objects.get(pk=store.get_target("teams.team", 9001))

    # Source graph: a submitted annotation whose save() already wrote its Score rows on the source.
    source_annotation = AnnotationFactory(data={"accuracy": "yes"})
    source_score = source_annotation.scores.get()

    # Target-side rows the annotation/score FKs resolve to, with their translations recorded.
    target_item = AnnotationItemFactory(team=target_team)
    target_reviewer = UserFactory()
    store.record("human_annotations.annotationitem", source_annotation.item_id, target_item.id)
    store.record("users.customuser", source_annotation.reviewer_id, target_reviewer.id)
    store.record("experiments.experimentsession", source_annotation.item.session_id, target_item.session_id)

    annotation_row = dict(build_resource_serializer(Annotation)(source_annotation).data)
    score_row = dict(build_resource_serializer(Score)(source_score).data)

    with mute_signals():
        importer.import_rows("human_annotations.annotation", [annotation_row])
        target_annotation_pk = store.get_target("human_annotations.annotation", source_annotation.id)
        # Importing the annotation already wrote a Score for (target review, "accuracy").
        side_effect_score = Score.objects.get(review_id=target_annotation_pk, name="accuracy")

        # Importing the source's own score row for the same field maps onto that row, not a duplicate.
        importer.import_rows("assessments.score", [score_row])

    assert Score.objects.filter(review_id=target_annotation_pk, name="accuracy").count() == 1
    # The imported score is recorded as the existing row, so a rerun resolves its FK straight to it.
    assert store.get_target("assessments.score", source_score.id) == side_effect_score.pk


# ---------------------------------------------------------------------------
# File content backfill
# ---------------------------------------------------------------------------

FILE_STORAGE = File._meta.get_field("file").storage


class _RecordingFetcher:
    """Stand-in for ResourceFetcher.get_file_content: records the source pks it's asked for and
    returns canned bytes (or raises, to simulate the source also missing the blob)."""

    def __init__(self, content=b"remote-bytes", error=None):
        self.content = content
        self.error = error
        self.calls = []

    def __call__(self, source_pk):
        self.calls.append(source_pk)
        if self.error is not None:
            raise self.error
        return self.content


def _file_row(source_id=501, *, path="synced-file.txt", external_id="", external_source=""):
    """A serialized files.file row whose ``file`` field is the stored relative ``path`` (as the export
    serializer emits it). The blob is not written to storage here, so importing it hits the missing-file
    path unless the caller seeds storage first. ``name`` is deterministic so tests can look the row up."""
    file = FileFactory.build()
    file.pk = source_id
    file.name = f"file-{source_id}"
    file.file = path
    file.external_id = external_id
    file.external_source = external_source
    file.working_version = None
    return dict(build_resource_serializer(File)(file, context={"public_key": None}).data)


def _clear_storage(*paths):
    # delete() is a safe no-op when the object is absent -- and unlike exists(), it doesn't depend on
    # the storage backend reporting existence reliably (real S3 is used in tests), so a stale blob
    # from a prior run can't make a "missing file" case silently find content.
    for path in paths:
        if path:
            FILE_STORAGE.delete(path)


def test_importing_file_backfills_missing_blob_from_the_source(store):
    """A file row whose blob isn't in this server's storage is backfilled: the bytes are fetched from
    the source's file content API (keyed by the source pk) and written at the stored path."""
    _clear_storage("backfilled-501.txt")
    fetcher = _RecordingFetcher(content=b"remote-bytes")
    importer = Importer(store, fetch_file_content=fetcher)
    importer.import_rows("teams.team", [_team_row()])

    importer.import_rows("files.file", [_file_row(501, path="backfilled-501.txt")])

    assert fetcher.calls == [501]
    file = File.objects.get(pk=store.get_target("files.file", 501))
    assert file.file.read() == b"remote-bytes"
    _clear_storage("backfilled-501.txt")


def test_importing_file_with_present_blob_does_not_fetch(store):
    """When the blob is already in storage (the operator copied it across), the file imports without
    touching the content API."""
    saved_path = FILE_STORAGE.save("present-502.txt", ContentFile(b"already-here"))
    fetcher = _RecordingFetcher()
    importer = Importer(store, fetch_file_content=fetcher)
    importer.import_rows("teams.team", [_team_row()])

    importer.import_rows("files.file", [_file_row(502, path=saved_path)])

    assert fetcher.calls == []
    file = File.objects.get(pk=store.get_target("files.file", 502))
    assert file.file.read() == b"already-here"
    _clear_storage(saved_path)


def test_importing_file_without_a_fetcher_raises_when_blob_missing(store):
    """With no fetcher wired in (the default), a missing blob surfaces as it does today -- File.save()
    reads its size from storage and fails -- rather than being silently swallowed."""
    _clear_storage("missing-503.txt")
    importer = Importer(store)
    importer.import_rows("teams.team", [_team_row()])

    with pytest.raises(FileNotFoundError):
        importer.import_rows("files.file", [_file_row(503, path="missing-503.txt")])


def test_importing_external_file_without_a_blob_is_imported_without_fetch(store):
    """External-source rows (e.g. OpenAI assistant files) carry no local blob, so File.save() never
    reads a size and there's nothing to backfill."""
    fetcher = _RecordingFetcher()
    importer = Importer(store, fetch_file_content=fetcher)
    importer.import_rows("teams.team", [_team_row()])

    importer.import_rows("files.file", [_file_row(504, path="", external_id="ext-1", external_source="openai")])

    assert fetcher.calls == []
    file = File.objects.get(pk=store.get_target("files.file", 504))
    assert not file.file


def test_file_not_found_on_source_imports_the_row_without_content_and_records_it(store):
    """A 404 from the content API means the source is missing the blob too. Rather than aborting,
    import the file's metadata without content (so references to it still resolve) and record it so
    the operator gets a report of what couldn't be recovered."""
    _clear_storage("gone-505.txt")
    fetcher = _RecordingFetcher(error=FileContentNotFound(505))
    importer = Importer(store, fetch_file_content=fetcher)
    importer.import_rows("teams.team", [_team_row()])

    importer.import_rows("files.file", [_file_row(505, path="gone-505.txt")])

    file = File.objects.get(pk=store.get_target("files.file", 505))
    assert not file.file
    assert "gone-505.txt" in importer.missing_files


def test_non_404_fetch_failure_aborts_import_and_commits_no_row(store):
    """A transport/server error isn't a 'file is gone' signal, so the sync still aborts and the row
    rolls back rather than silently landing a file with no content."""
    _clear_storage("fail-507.txt")
    fetcher = _RecordingFetcher(error=requests.HTTPError("500"))
    importer = Importer(store, fetch_file_content=fetcher)
    importer.import_rows("teams.team", [_team_row()])

    with pytest.raises(requests.HTTPError):
        importer.import_rows("files.file", [_file_row(507, path="fail-507.txt")])

    assert store.get_target("files.file", 507) is None
    assert not File.objects.filter(name="file-507").exists()


# ---------------------------------------------------------------------------
# Whole-manifest round-trip test
# ---------------------------------------------------------------------------

FACTORIES = {
    "users.customuser": UserFactory,
    "service_providers.llmprovider": LlmProviderFactory,
    "service_providers.voiceprovider": VoiceProviderFactory,
    "service_providers.messagingprovider": MessagingProviderFactory,
    "service_providers.authprovider": AuthProviderFactory,
    "service_providers.traceprovider": TraceProviderFactory,
    "service_providers.llmprovidermodel": LlmProviderModelFactory,
    "service_providers.embeddingprovidermodel": EmbeddingProviderModelFactory,
    "experiments.syntheticvoice": SyntheticVoiceFactory,
    "custom_actions.customaction": CustomActionFactory,
    "experiments.sourcematerial": SourceMaterialFactory,
    "experiments.consentform": ConsentFormFactory,
    "annotations.tag": EvaluationTagFactory,
    "documents.collection": CollectionFactory,
    "files.file": FileFactory,
    "documents.collectionfile": CollectionFileFactory,
    "documents.documentsource": DocumentSourceFactory,
    "files.filechunkembedding": FileChunkEmbeddingFactory,
    "ocs_notifications.eventtype": EventTypeFactory,
    "ocs_notifications.usernotificationpreferences": UserNotificationPreferencesFactory,
    "pipelines.pipeline": PipelineFactory,
    "pipelines.node": NodeFactory,
    "custom_actions.customactionoperation": CustomActionOperationFactory,
    "experiments.experiment": ExperimentFactory,
    "bot_channels.experimentchannel": ExperimentChannelFactory,
    "events.eventaction": EventActionFactory,
    "events.statictrigger": StaticTriggerFactory,
    "events.timeouttrigger": TimeoutTriggerFactory,
    "experiments.participant": ParticipantFactory,
    "experiments.participantdata": ParticipantDataFactory,
    "chat.chat": ChatFactory,
    "experiments.experimentsession": ExperimentSessionFactory,
    "chat.chatattachment": ChatAttachmentFactory,
    "chat.chatmessage": ChatMessageFactory,
    "trace.trace": TraceFactory,
    "pipelines.pipelinechathistory": PipelineChatHistoryFactory,
    "pipelines.pipelinechatmessages": PipelineChatMessagesFactory,
    "events.scheduledmessage": ScheduledMessageFactory,
    "ocs_notifications.notificationevent": NotificationEventFactory,
    "ocs_notifications.eventuser": EventUserFactory,
    "evaluations.evaluator": EvaluatorFactory,
    "evaluations.evaluationmessage": EvaluationMessageFactory,
    "evaluations.evaluationdataset": EvaluationDatasetFactory,
    "evaluations.datasetautopopulationrule": DatasetAutoPopulationRuleFactory,
    "evaluations.evaluationconfig": EvaluationConfigFactory,
    "evaluations.evaluatortagrule": EvaluatorTagRuleFactory,
    "evaluations.evaluationrun": EvaluationRunFactory,
    "evaluations.evaluationresult": EvaluationResultFactory,
    "evaluations.evaluationrunaggregate": EvaluationRunAggregateFactory,
    "evaluations.appliedtag": AppliedTagFactory,
    "human_annotations.annotationqueue": AnnotationQueueFactory,
    "human_annotations.annotationitem": AnnotationItemFactory,
    "human_annotations.annotation": AnnotationFactory,
    "human_annotations.annotationqueueaggregate": AnnotationQueueAggregateFactory,
    "analysis.transcriptanalysis": TranscriptAnalysisFactory,
    "analysis.analysisquery": AnalysisQueryFactory,
    "cost_tracking.pricingrule": PricingRuleFactory,
    "cost_tracking.usagerecord": UsageRecordFactory,
    "annotations.customtaggeditem": CustomTaggedItemFactory,
    "annotations.usercomment": UserCommentFactory,
    "assessments.score": ScoreFactory,
}

BUILD_OVERRIDES = {
    "human_annotations.annotationqueue": {"created_by": None},
    "human_annotations.annotationitem": {"queue__created_by": None},
    "human_annotations.annotation": {"item__queue__created_by": None},
    "human_annotations.annotationqueueaggregate": {"queue__created_by": None},
    "events.scheduledmessage": {
        "custom_schedule_params": {"name": "Test", "time_period": "days", "frequency": 1, "repetitions": 1}
    },
}


def _commit_file_fields(obj) -> None:
    """Persist factory-built in-memory files to storage so the serialized row carries a real stored
    path and a model whose save() reads file.size (e.g. File) finds the blob on import."""
    for field in obj._meta.concrete_fields:
        if isinstance(field, models.FileField):
            fieldfile = getattr(obj, field.attname)
            if fieldfile and not fieldfile._committed:
                fieldfile.save(fieldfile.name, fieldfile, save=False)


def _serialized_row(model_label, mock_ids, manifest_labels, public_key):
    """The row exactly as the endpoint would serve it: a factory-built instance carrying mock source
    ids -- its own pk and every synced FK (self-refs and FKs to unsynced models nulled) -- run
    through the model's real export serializer."""
    model = entry_model(model_label)
    obj = FACTORIES[model_label].build(**BUILD_OVERRIDES.get(model_label, {}))
    obj.pk = mock_ids[model_label]
    gfk_pairs = generic_fk_fields(model)
    gfk_columns = {name for pair in gfk_pairs for name in pair}
    for field in model._meta.concrete_fields:
        if isinstance(field, models.ForeignKey) and not field.primary_key and field.name not in gfk_columns:
            related = field.related_model._meta.label_lower
            target = mock_ids[related] if related != model_label and related in manifest_labels else None
            setattr(obj, field.attname, target)
    # Point each generic FK at the mock source id of whatever model its content type names, so the
    # importer's translation resolves it (the factory's content_type stays; only the id is mocked).
    for ct_field, fk_field in gfk_pairs:
        ct = getattr(obj, ct_field)
        label = f"{ct.app_label}.{ct.model}"
        setattr(obj, fk_field, mock_ids[label] if label in manifest_labels else None)
    _commit_file_fields(obj)
    return dict(build_resource_serializer(model)(obj, context={"public_key": public_key}).data)


def test_registry_covers_every_manifest_entry():
    """Adding a manifest entry without a factory here must fail loudly rather than silently skip it."""
    assert {e.model for e in MANIFEST_ENTRIES} - set(FACTORIES) == set()


def test_every_manifest_entry_imports(store, keypair):
    """Serialize one factory-built row per manifest model through its real export serializer (exactly
    as the endpoint would), import them all in manifest order, and assert every entry landed -- a
    round-trip smoke test over the whole manifest. Runs under mute_signals like the real command, so
    it also catches any model that secretly relies on a signal to populate a field on import."""
    public_key, private_key = keypair
    manifest_labels = {e.model for e in MANIFEST_ENTRIES} | {"teams.team"}
    importer = Importer(store, private_key=private_key)

    mock_ids = {entry.model: 1000 + i for i, entry in enumerate(MANIFEST_ENTRIES)}
    mock_ids["teams.team"] = 999  # the anchor team's source pk
    with mute_signals():
        team = TeamFactory.build()
        team.pk = 999
        team_row = dict(build_resource_serializer(Team)(team, context={"public_key": public_key}).data)
        importer.import_rows("teams.team", [team_row])

        for entry in MANIFEST_ENTRIES:
            row = _serialized_row(entry.model, mock_ids, manifest_labels, public_key)
            importer.import_rows(entry.model, [row])

    not_imported = []
    for entry in MANIFEST_ENTRIES:
        target = store.get_target(entry.model, mock_ids[entry.model])
        if target is None:
            not_imported.append(f"{entry.model}: no target recorded")
        elif not entry_model(entry.model).objects.filter(pk=target).exists():
            not_imported.append(f"{entry.model}: target {target} not in the database")
    assert not not_imported, "Manifest entries that failed to import:\n" + "\n".join(not_imported)
    assert store.has_unfilled_targets() is False
