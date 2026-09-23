"""The row sets one chatbot selection resolves to. Each test builds two chatbots and asserts the
other one's rows stay out."""

import pytest

from apps.chat.models import Chat
from apps.teams.export import manifest
from apps.teams.export.chatbot_scope import CHATBOT_SCOPE_REGISTRY, ScopeClass, build_scope
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.documents import CollectionFactory, CollectionFileFactory
from apps.utils.factories.events import StaticTriggerFactory
from apps.utils.factories.experiment import (
    ChatMessageFactory,
    ConsentFormFactory,
    ExperimentFactory,
    ExperimentSessionFactory,
    ParticipantDataFactory,
    ParticipantFactory,
    SourceMaterialFactory,
)
from apps.utils.factories.files import FileFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamFactory

pytestmark = pytest.mark.django_db


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


def test_sessions_chats_and_messages_follow_the_family():
    team = TeamFactory()
    mine = ExperimentFactory(team=team)
    theirs = ExperimentFactory(team=team)
    team.exportable_experiments.add(mine)
    my_session = ExperimentSessionFactory(experiment=mine, team=team)
    their_session = ExperimentSessionFactory(experiment=theirs, team=team)
    my_message = ChatMessageFactory(chat=my_session.chat)
    their_message = ChatMessageFactory(chat=their_session.chat)

    scope = build_scope(team)

    assert set(scope.sessions.values_list("pk", flat=True)) == {my_session.id}
    assert set(Chat.objects.filter(pk__in=scope.chats).values_list("pk", flat=True)) == {my_session.chat_id}
    assert set(scope.chat_messages.values_list("pk", flat=True)) == {my_message.id}
    assert their_message.id not in set(scope.chat_messages.values_list("pk", flat=True))


def test_participants_include_ones_reachable_only_through_participant_data():
    """ParticipantData.participant is non-null, so a participant with data for the chatbot but no
    session must still be exported or resolve_fk raises on import."""

    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)
    with_session = ExperimentSessionFactory(experiment=chatbot, team=team).participant
    data_only = ParticipantFactory(team=team)
    ParticipantDataFactory(team=team, experiment=chatbot, participant=data_only)
    unrelated = ParticipantFactory(team=team)

    ids = set(build_scope(team).participants.values_list("pk", flat=True))

    assert {with_session.id, data_only.id} <= ids
    assert unrelated.id not in ids


def test_channels_cover_the_chatbots_own_and_the_teams_shared_ones():
    team = TeamFactory()
    mine = ExperimentFactory(team=team)
    theirs = ExperimentFactory(team=team)
    team.exportable_experiments.add(mine)
    my_channel = ExperimentChannelFactory(team=team, experiment=mine)
    their_channel = ExperimentChannelFactory(team=team, experiment=theirs)
    shared = ExperimentChannelFactory(team=team, experiment=None)

    ids = set(build_scope(team).channel_ids)

    assert {my_channel.id, shared.id} <= ids
    assert their_channel.id not in ids


def test_pipeline_and_node_closure_follows_the_chatbots_pipeline():
    team = TeamFactory()
    pipeline = PipelineFactory(team=team)
    node = NodeFactory(pipeline=pipeline)
    chatbot = ExperimentFactory(team=team, pipeline=pipeline)
    other_pipeline = PipelineFactory(team=team)
    ExperimentFactory(team=team, pipeline=other_pipeline)
    team.exportable_experiments.add(chatbot)

    scope = build_scope(team)

    assert pipeline.id in scope.pipeline_ids
    assert other_pipeline.id not in scope.pipeline_ids
    assert node.id in scope.node_ids


def test_pipeline_closure_includes_one_named_in_an_event_action():

    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)
    started = PipelineFactory(team=team)
    trigger = StaticTriggerFactory(experiment=chatbot)
    trigger.action.action_type = "pipeline_start"
    trigger.action.params = {"pipeline_id": started.id}
    trigger.action.save()

    assert started.id in build_scope(team).pipeline_ids


def test_referenced_resources_include_their_working_versions():
    """A published resource carries a self-referential working_version FK; exporting it without its
    working row leaves a link the target cannot resolve."""
    team = TeamFactory()
    working_pipeline = PipelineFactory(team=team)
    published_pipeline = PipelineFactory(team=team, working_version=working_pipeline)
    chatbot = ExperimentFactory(team=team, pipeline=published_pipeline)
    team.exportable_experiments.add(chatbot)

    assert {working_pipeline.id, published_pipeline.id} <= set(build_scope(team).pipeline_ids)


def test_provider_closure_follows_the_nodes_and_the_chatbot():
    team = TeamFactory()
    provider = LlmProviderFactory(team=team)
    unused = LlmProviderFactory(team=team)
    pipeline = PipelineFactory(team=team)
    NodeFactory(pipeline=pipeline, llm_provider=provider)
    chatbot = ExperimentFactory(team=team, pipeline=pipeline)
    team.exportable_experiments.add(chatbot)

    ids = build_scope(team).llm_provider_ids

    assert provider.id in ids
    assert unused.id not in ids


def test_collection_and_file_closure_follows_the_nodes():
    team = TeamFactory()
    collection = CollectionFactory(team=team, llm_provider=None, embedding_provider_model=None)
    used_file = FileFactory(team=team)
    CollectionFileFactory(collection=collection, file=used_file)
    unused_file = FileFactory(team=team)
    pipeline = PipelineFactory(team=team)
    NodeFactory(pipeline=pipeline, collection=collection)
    chatbot = ExperimentFactory(team=team, pipeline=pipeline)
    team.exportable_experiments.add(chatbot)

    scope = build_scope(team)

    assert collection.id in scope.collection_ids
    file_ids = set(scope.files.values_list("pk", flat=True))
    assert used_file.id in file_ids
    assert unused_file.id not in file_ids


def test_file_closure_includes_chat_attachments():

    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)
    session = ExperimentSessionFactory(experiment=chatbot, team=team)
    attachment = session.chat.attachments.create(tool_type="code_interpreter")
    attached = FileFactory(team=team)
    attachment.files.add(attached)

    assert attached.id in set(build_scope(team).files.values_list("pk", flat=True))


def test_consent_form_and_source_material_follow_the_chatbot():
    team = TeamFactory()
    consent = ConsentFormFactory(team=team)
    material = SourceMaterialFactory(team=team)
    pipeline = PipelineFactory(team=team)
    NodeFactory(pipeline=pipeline, source_material=material)
    chatbot = ExperimentFactory(team=team, pipeline=pipeline, consent_form=consent)
    team.exportable_experiments.add(chatbot)

    scope = build_scope(team)

    assert consent.id in scope.consent_form_ids
    assert material.id in scope.source_material_ids


def test_scope_sets_are_computed_once(django_assert_num_queries):
    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)
    scope = build_scope(team)

    first = scope.experiment_ids
    with django_assert_num_queries(0):
        assert scope.experiment_ids == first == [chatbot.id]


def test_every_manifest_model_has_a_scope_rule():
    """A model added to the manifest must be classified, or a chatbot-scoped sync silently serves it
    team-wide and drags in another chatbot's rows."""
    unclassified = {e.model for e in manifest.MANIFEST_ENTRIES} - set(CHATBOT_SCOPE_REGISTRY)
    assert not unclassified, "Add these to CHATBOT_SCOPE_REGISTRY: " + ", ".join(sorted(unclassified))


def test_scope_registry_has_no_entries_for_unsynced_models():
    assert set(CHATBOT_SCOPE_REGISTRY) <= {e.model for e in manifest.MANIFEST_ENTRIES}


def test_excluded_rules_carry_no_query_and_the_others_do():
    for label, rule in CHATBOT_SCOPE_REGISTRY.items():
        if rule.scope_class is ScopeClass.EXCLUDED:
            assert rule.build_q is None, label
        else:
            assert rule.build_q is not None, label


def test_the_excluded_classes_are_exactly_the_brief():
    excluded = {label for label, rule in CHATBOT_SCOPE_REGISTRY.items() if rule.scope_class is ScopeClass.EXCLUDED}
    assert {label.split(".")[0] for label in excluded} == {"evaluations", "human_annotations", "analysis"}


def test_participants_are_referenced_so_a_selection_rereads_them():
    """A participant who first talked to another chatbot joins the scope without their row changing."""
    assert CHATBOT_SCOPE_REGISTRY["experiments.participant"].scope_class is ScopeClass.REFERENCED


def test_every_scope_rule_builds_a_runnable_queryset():
    """A misspelt lookup path raises FieldError only when the queryset runs. Running each rule once
    against an empty database is enough to catch that."""
    team = TeamFactory()
    team.exportable_experiments.add(ExperimentFactory(team=team))
    scope = build_scope(team)

    for entry in manifest.MANIFEST_ENTRIES:
        list(manifest.scoped_queryset(entry, team, scope)[:1])


def test_a_malformed_pipeline_id_in_an_event_action_is_ignored():
    """One bad legacy value must not break the scope every resource request builds."""
    team = TeamFactory()
    chatbot = ExperimentFactory(team=team)
    team.exportable_experiments.add(chatbot)
    trigger = StaticTriggerFactory(experiment=chatbot)
    trigger.action.action_type = "pipeline_start"
    trigger.action.params = {"pipeline_id": "not-a-number"}
    trigger.action.save()

    assert build_scope(team).pipeline_ids == [chatbot.pipeline_id]
