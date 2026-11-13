import pytest
from field_audit.models import AuditAction

from apps.events.models import EventActionType, StaticTriggerType
from apps.experiments.models import Experiment
from apps.pipelines.models import Node
from apps.pipelines.nodes.nodes import LLMResponseWithPrompt
from apps.pipelines.tests.utils import (
    create_pipeline_model,
    end_node,
    llm_response_with_prompt_node,
    start_node,
)
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.events import EventActionFactory, StaticTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory, SourceMaterialFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


def _source_with_pipeline():
    source = ExperimentFactory.create(name="Source", seed_message="hello")
    team = source.team
    provider = LlmProviderFactory.create(team=team)
    provider_model = LlmProviderModelFactory.create(team=team)
    source_material = SourceMaterialFactory.create(team=team)
    nodes = [
        start_node(),
        llm_response_with_prompt_node(
            str(provider.id), str(provider_model.id), source_material_id=str(source_material.id)
        ),
        end_node(),
    ]
    create_pipeline_model(nodes, pipeline=source.pipeline)
    source.pipeline.save(update_fields=["data"])
    return source, nodes


def _llm_node(experiment):
    return Node.objects.get(pipeline=experiment.pipeline, type=LLMResponseWithPrompt.__name__)


@pytest.mark.django_db()
def test_copy_records_its_source():
    source = ExperimentFactory.create()
    copy = source.create_new_version(is_copy=True, name="Test bot")
    copy_of_copy = copy.create_new_version(is_copy=True, name="Test bot 2")

    assert copy.cloned_from_id == source.id
    assert copy_of_copy.cloned_from_id == copy.id


@pytest.mark.django_db()
def test_published_version_does_not_count_as_a_copy():
    source = ExperimentFactory.create()
    copy = source.create_new_version(is_copy=True, name="Test bot")

    assert source.create_new_version().cloned_from_id is None
    assert copy.create_new_version().cloned_from_id is None


@pytest.mark.django_db()
def test_overwrite_takes_the_source_content_and_keeps_the_copy_name():
    source, nodes = _source_with_pipeline()
    copy = source.create_new_version(is_copy=True, name="Test bot")
    copy_pipeline_id = copy.pipeline_id

    source.seed_message = "changed"
    source.save()
    other_material = SourceMaterialFactory.create(team=source.team)
    nodes[1]["params"]["source_material_id"] = str(other_material.id)
    create_pipeline_model(nodes, pipeline=source.pipeline)

    copy.overwrite_from_source()
    copy.refresh_from_db()

    assert copy.name == "Test bot"
    assert copy.seed_message == "changed"
    assert copy.pipeline_id == copy_pipeline_id
    assert _llm_node(copy).params["source_material_id"] == other_material.id


@pytest.mark.django_db()
def test_overwrite_gives_the_copy_its_own_node_ids():
    source, _nodes = _source_with_pipeline()
    copy = source.create_new_version(is_copy=True, name="Test bot")

    copy.overwrite_from_source()

    source_ids = set(source.pipeline.node_ids)
    copy_ids = set(Experiment.objects.get(id=copy.id).pipeline.node_ids)
    assert len(copy_ids) == len(source_ids)
    assert copy_ids.isdisjoint(source_ids)


@pytest.mark.django_db()
def test_overwrite_keeps_channels_and_published_versions():
    source, _nodes = _source_with_pipeline()
    copy = source.create_new_version(is_copy=True, name="Test bot")
    channel = ExperimentChannelFactory.create(team=copy.team, experiment=copy)
    published = copy.create_new_version(make_default=True)
    published_node_ids = set(published.pipeline.node_ids)

    copy.overwrite_from_source()

    channel.refresh_from_db()
    published.refresh_from_db()
    assert channel.experiment_id == copy.id
    assert published.is_default_version is True
    assert set(published.pipeline.node_ids) == published_node_ids


@pytest.mark.django_db()
def test_overwrite_replaces_the_copy_triggers():
    source, _nodes = _source_with_pipeline()
    copy = source.create_new_version(is_copy=True, name="Test bot")
    own_trigger = StaticTriggerFactory(experiment=copy, type=StaticTriggerType.CONVERSATION_START)
    StaticTriggerFactory(
        experiment=source,
        type=StaticTriggerType.CONVERSATION_END,
        action=EventActionFactory(action_type=EventActionType.LOG, params={"key": "value"}),
    )

    copy.overwrite_from_source()

    own_trigger.refresh_from_db()
    assert own_trigger.is_archived
    assert [t.type for t in copy.static_triggers.all()] == [StaticTriggerType.CONVERSATION_END]


@pytest.mark.parametrize(
    "make_target",
    [
        pytest.param(lambda source: ExperimentFactory.create(team=source.team), id="not-a-copy"),
        pytest.param(
            lambda source: source.create_new_version(is_copy=True, name="Test bot").create_new_version(),
            id="version-of-a-copy",
        ),
    ],
)
@pytest.mark.django_db()
def test_overwrite_rejects_a_chatbot_that_is_not_a_working_copy(make_target):
    source = ExperimentFactory.create()
    target = make_target(source)

    with pytest.raises(ValueError, match="working copy"):
        target.overwrite_from_source()


@pytest.mark.django_db()
def test_overwrite_gives_a_copy_without_a_pipeline_its_own_copy():
    source, _nodes = _source_with_pipeline()
    copy = source.create_new_version(is_copy=True, name="Test bot")
    Experiment.objects.filter(id=copy.id).update(pipeline=None, audit_action=AuditAction.AUDIT)
    copy.refresh_from_db()

    copy.overwrite_from_source()

    copy.refresh_from_db()
    assert copy.pipeline_id not in (None, source.pipeline_id)
    assert set(copy.pipeline.node_ids).isdisjoint(source.pipeline.node_ids)
    assert len(copy.pipeline.node_ids) == len(source.pipeline.node_ids)


@pytest.mark.django_db()
def test_overwrite_rejects_a_source_without_a_pipeline():
    source = ExperimentFactory.create()
    copy = source.create_new_version(is_copy=True, name="Test bot")
    Experiment.objects.filter(id=source.id).update(pipeline=None, audit_action=AuditAction.AUDIT)
    copy.refresh_from_db()

    with pytest.raises(ValueError, match="has no pipeline"):
        copy.overwrite_from_source()


@pytest.mark.django_db()
def test_overwrite_rejects_an_archived_source():
    source = ExperimentFactory.create()
    copy = source.create_new_version(is_copy=True, name="Test bot")
    source.archive()
    copy.refresh_from_db()

    with pytest.raises(ValueError, match="archived"):
        copy.overwrite_from_source()
