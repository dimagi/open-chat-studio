from unittest.mock import patch

import pytest

from apps.events.models import EventActionType
from apps.experiments.models import SourceMaterial
from apps.pipelines.models import Node
from apps.pipelines.nodes.nodes import LLMResponseWithPrompt
from apps.pipelines.repository import ORMRepository
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.events import EventActionFactory, ExperimentFactory, StaticTriggerFactory
from apps.utils.factories.experiment import SourceMaterialFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory


@pytest.mark.django_db()
def test_archive_pipeline_archives_nodes_as_well():
    pipeline = PipelineFactory.create()
    assert pipeline.node_set.count() > 0
    pipeline.archive()
    assert pipeline.node_set.count() == 0


@pytest.mark.django_db()
class TestUnarchivePipeline:
    def test_restores_the_nodes_archived_with_it(self):
        pipeline = PipelineFactory.create()
        node_count = pipeline.node_set.count()
        pipeline.archive()

        pipeline.unarchive()

        assert pipeline.is_archived is False
        assert pipeline.node_set.count() == node_count

    def test_leaves_a_node_removed_from_the_canvas_archived(self):
        """A removed node with versions stays as an archived row; restoring the pipeline must not revive it."""
        pipeline = PipelineFactory.create()
        removed = NodeFactory.create(pipeline=pipeline)
        pipeline.create_new_version()
        pipeline.update_nodes_from_data({flow_id: None for flow_id in pipeline.node_ids if flow_id != removed.flow_id})
        removed.refresh_from_db()
        assert removed.is_archived is True
        pipeline.archive()

        pipeline.unarchive()

        removed.refresh_from_db()
        assert removed.is_archived is True
        assert removed.flow_id not in pipeline.node_ids

    def test_a_restored_node_removed_later_stays_removed(self):
        """Restoring must clear the mark it restores by, or the next restore would revive a removed node."""
        pipeline = PipelineFactory.create()
        node = NodeFactory.create(pipeline=pipeline)
        pipeline.create_new_version()
        pipeline.archive()
        pipeline.unarchive()
        pipeline.update_nodes_from_data({flow_id: None for flow_id in pipeline.node_ids if flow_id != node.flow_id})
        pipeline.archive()

        pipeline.unarchive()

        node.refresh_from_db()
        assert node.is_archived is True

    def test_restores_every_node_of_a_version_pipeline(self):
        """Version pipelines archived before the mark existed carry unmarked archived nodes."""
        pipeline = PipelineFactory.create()
        version = pipeline.create_new_version()
        node_count = version.node_set.count()
        version.archive()
        Node.objects.get_all().filter(pipeline=version).update(archived_with_pipeline=False)

        version.unarchive()

        assert version.node_set.count() == node_count


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "archive_working", [pytest.param(True, id="whole-chatbot"), pytest.param(False, id="one-version")]
)
def test_unarchive_restores_the_source_material_a_published_version_uses(archive_working):
    source_material = SourceMaterialFactory.create()
    chatbot = ExperimentFactory.create(team=source_material.team)
    node_type = LLMResponseWithPrompt.__name__
    params = {"source_material_id": str(source_material.id)}
    NodeFactory.create(type=node_type, pipeline=chatbot.pipeline, params=params)
    version = chatbot.create_new_version()
    material_id = int(version.pipeline.node_set.get(type=node_type).params["source_material_id"])
    assert material_id != source_material.id
    archived = chatbot if archive_working else version
    archived.archive()
    assert SourceMaterial.objects.get_all().get(id=material_id).is_archived is True

    archived.unarchive()

    assert ORMRepository().get_source_material(material_id).id == material_id


@pytest.mark.django_db()
class TestArchivingNodes:
    @patch("apps.pipelines.models.Node._archive_related_params")
    def test_archive_related_objects_conditionally(self, archive_related_params):
        """Related objects should only be archived when the node is a version"""
        pipeline = PipelineFactory.create()
        node = NodeFactory.create(pipeline=pipeline)
        node_version = NodeFactory.create(pipeline=pipeline, working_version=node)

        assert node.archive() is True
        archive_related_params.assert_not_called()

        assert node_version.archive() is True
        archive_related_params.assert_called()

    def test_archive_related_objects(self):
        # Setup related objects
        collection = CollectionFactory.create()
        collection_index = CollectionFactory.create(
            is_index=True, openai_vector_store_id="v-123", llm_provider=LlmProviderFactory.create()
        )
        source_material = SourceMaterialFactory.create()

        # Build the pipeline
        pipeline = PipelineFactory.create()
        NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={
                "collection_id": str(collection.id),
                "collection_index_ids": [str(collection_index.id)],
                "source_material_id": str(source_material.id),
            },
        )
        pipeline.create_new_version()

        pipeline.archive()

        # Ensure that the working versions are not archived
        collection.refresh_from_db()
        collection_index.refresh_from_db()

        # ADR-0031: media + index collections are live shared resources — never versioned per bot,
        # so the working collections are untouched and no collection versions exist.
        assert collection.is_archived is False
        assert not collection.versions.exists()
        assert collection_index.is_archived is False
        assert not collection_index.versions.exists()

    def test_archive_legacy_frozen_index_version(self):
        """
        LEGACY data: a pre-ADR-0031 node may reference both the live working index id and a
        frozen index version id in collection_index_ids. Archiving the pipeline must archive the
        frozen copy (is_a_version=True) while leaving the live working index (is_a_version=False)
        untouched. Including both ids makes the is_a_version guard load-bearing: dropping the guard
        would wrongly archive the working index and fail this test.
        """
        # Working index collection (is_a_version=False)
        collection_index = CollectionFactory.create(is_index=True)
        # Frozen version of the index (is_a_version=True) — simulates the legacy per-bot copy
        frozen_index = collection_index.create_new_version()
        assert frozen_index.is_a_version, "pre-condition: frozen_index must be a version"

        # Node references BOTH the live working id and the frozen version id (legacy stored data)
        pipeline = PipelineFactory.create()
        NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={"collection_index_ids": [collection_index.id, frozen_index.id]},
        )

        # Version the pipeline — the version node copies the param verbatim
        pipeline.create_new_version()

        # Archive the pipeline (working version → archives all version nodes too)
        pipeline.archive()

        frozen_index.refresh_from_db()
        collection_index.refresh_from_db()
        # The frozen per-bot copy is archived (legacy cleanup branch)
        assert frozen_index.is_archived is True
        # The live working index is protected by the is_a_version guard
        assert collection_index.is_archived is False

    def test_archive_legacy_frozen_media_version(self):
        """
        LEGACY data: a pre-ADR-0031 node may reference a frozen media collection version in
        collection_id. Archiving the pipeline must archive that frozen copy (is_a_version=True)
        while leaving a live working media collection (referenced by another node) untouched.
        The working-media node makes the is_a_version guard load-bearing: dropping the guard would
        wrongly archive the working media and fail this test.
        """
        node_type = LLMResponseWithPrompt.__name__
        working_media = CollectionFactory.create(is_index=False)
        frozen_media = working_media.create_new_version()
        assert frozen_media.is_a_version, "pre-condition: frozen_media must be a version"

        pipeline = PipelineFactory.create()
        # Node A references the live working media id; Node B references the frozen version id.
        NodeFactory.create(type=node_type, pipeline=pipeline, params={"collection_id": str(working_media.id)})
        NodeFactory.create(type=node_type, pipeline=pipeline, params={"collection_id": str(frozen_media.id)})

        pipeline.create_new_version()
        pipeline.archive()

        working_media.refresh_from_db()
        frozen_media.refresh_from_db()
        assert frozen_media.is_archived is True
        assert working_media.is_archived is False

    def test_archiving_one_node_version_leaves_a_shared_source_material_version_intact(self):
        """REUSE_UNCHANGED can leave one version shared by two separately-published nodes."""
        source_material = SourceMaterialFactory.create()

        pipeline_a = PipelineFactory.create()
        node_a = NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline_a,
            params={"source_material_id": str(source_material.id)},
        )
        node_a_version = node_a.create_new_version()
        shared_version_id = node_a_version.params["source_material_id"]
        assert shared_version_id != str(source_material.id), "pre-condition: first publish versions it"

        pipeline_b = PipelineFactory.create()
        node_b = NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline_b,
            params={"source_material_id": str(source_material.id)},
        )
        node_b_version = node_b.create_new_version()
        assert node_b_version.params["source_material_id"] == shared_version_id, (
            "pre-condition: unchanged content reuses the existing version rather than creating a new one"
        )

        node_a_version.archive()
        shared_version = SourceMaterial.objects.get(id=shared_version_id)
        assert shared_version.is_archived is False, "node_b_version still references this version"

        node_b_version.archive()
        shared_version.refresh_from_db()
        assert shared_version.is_archived is True


@pytest.mark.django_db()
def test_archive_pipeline_refuses_while_referenced():
    pipeline = PipelineFactory.create()
    start_pipeline__action = EventActionFactory.create(
        action_type=EventActionType.PIPELINE_START,
        params={
            "pipeline_id": pipeline.id,
        },
    )
    experiment1 = ExperimentFactory.create()
    experiment2 = ExperimentFactory.create()
    static_trigger = StaticTriggerFactory.create(experiment=experiment2, action=start_pipeline__action)

    # Experiment and Static trigger still uses it
    assert pipeline.archive() is False

    experiment1.archive()
    # Static trigger from experiment2 still uses it
    assert pipeline.archive() is False

    static_trigger.archive()
    # Nothing uses it, so archive it
    assert pipeline.archive() is True
