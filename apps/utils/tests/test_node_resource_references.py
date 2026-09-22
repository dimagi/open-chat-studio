"""The resource-reference helpers in ``apps.utils.deletion``, which read the node FK columns.

An id that params still carries but the FK column no longer does is not a reference.
"""

import pytest

from apps.pipelines.models import Node
from apps.utils.deletion import (
    get_related_experiment_versions_queryset,
    get_related_objects,
    get_related_pipeline_nodes_queryset,
)
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import ExperimentFactory, SourceMaterialFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory, LlmProviderModelFactory


@pytest.mark.django_db()
class TestNodeResourceLookups:
    def test_scalar_fk_reference_is_found(self):
        source_material = SourceMaterialFactory.create()
        pipeline = PipelineFactory.create(team=source_material.team)
        node = NodeFactory.create(pipeline=pipeline, params={"source_material_id": source_material.id})

        assert list(get_related_pipeline_nodes_queryset(source_material, "source_material")) == [node]

    def test_m2m_reference_is_found(self):
        collection = CollectionFactory.create(is_index=True)
        pipeline = PipelineFactory.create(team=collection.team)
        node = NodeFactory.create(pipeline=pipeline, params={"collection_index_ids": [collection.id]})

        nodes = get_related_pipeline_nodes_queryset(collection, "collection", "collection_indexes")
        assert list(nodes) == [node]

    def test_node_referencing_through_both_fields_is_reported_once(self):
        collection = CollectionFactory.create(is_index=True)
        pipeline = PipelineFactory.create(team=collection.team)
        node = NodeFactory.create(
            pipeline=pipeline,
            params={"collection_id": collection.id, "collection_index_ids": [collection.id]},
        )

        nodes = get_related_pipeline_nodes_queryset(collection, "collection", "collection_indexes")
        assert list(nodes) == [node]

    def test_params_id_without_the_fk_is_not_a_reference(self):
        """The FK column decides. A params id the column doesn't carry is not a reference."""
        source_material = SourceMaterialFactory.create()
        pipeline = PipelineFactory.create(team=source_material.team)
        node = NodeFactory.create(pipeline=pipeline, params={"source_material_id": source_material.id})
        Node.objects.filter(id=node.id).update(source_material=None)

        assert not get_related_pipeline_nodes_queryset(source_material, "source_material").exists()
        assert not get_related_experiment_versions_queryset(source_material, "source_material").exists()

    def test_experiment_versions_reference_through_a_collection_index(self):
        collection = CollectionFactory.create(is_index=True)
        pipeline = PipelineFactory.create(team=collection.team)
        NodeFactory.create(pipeline=pipeline, params={"collection_index_ids": [collection.id]})
        experiment = ExperimentFactory.create(pipeline=pipeline, team=collection.team)

        experiments = get_related_experiment_versions_queryset(collection, "collection", "collection_indexes")
        assert list(experiments) == [experiment]

    def test_unreferenced_resource_has_no_related_experiments(self):
        collection = CollectionFactory.create(is_index=True)

        experiments = get_related_experiment_versions_queryset(collection, "collection", "collection_indexes")
        assert not experiments.exists()


@pytest.mark.django_db()
class TestGetRelatedObjectsThroughNodeFks:
    def test_pipeline_is_reported_once_per_provider(self):
        """The reverse FK is the only source of node references, so no pipeline is listed twice."""
        provider = LlmProviderFactory.create()
        pipeline = PipelineFactory.create(team=provider.team)
        NodeFactory.create(pipeline=pipeline, params={"llm_provider_id": provider.id})
        NodeFactory.create(pipeline=pipeline, params={"llm_provider_id": provider.id})

        assert [obj.id for obj in get_related_objects(provider)] == [pipeline.id]

    def test_provider_model_in_use_blocks_delete(self):
        provider_model = LlmProviderModelFactory.create()
        pipeline = PipelineFactory.create(team=provider_model.team)
        NodeFactory.create(pipeline=pipeline, params={"llm_provider_model_id": provider_model.id})

        assert provider_model.has_related_objects() is True

    def test_provider_model_referenced_only_by_params_is_deletable(self):
        provider_model = LlmProviderModelFactory.create()
        pipeline = PipelineFactory.create(team=provider_model.team)
        node = NodeFactory.create(pipeline=pipeline, params={"llm_provider_model_id": provider_model.id})
        Node.objects.filter(id=node.id).update(llm_provider_model=None)

        assert node.params["llm_provider_model_id"] == provider_model.id
        assert provider_model.has_related_objects() is False
        assert get_related_objects(provider_model) == []


@pytest.mark.django_db()
class TestNodeFactoryResourceSync:
    def test_a_directly_passed_column_does_not_blank_the_others(self):
        """Passing one resource must still sync the resources params names."""
        provider = LlmProviderFactory.create()
        source_material = SourceMaterialFactory.create(team=provider.team)
        collection = CollectionFactory.create(team=provider.team, is_index=True)

        node = NodeFactory.create(
            llm_provider=provider,
            params={"source_material_id": source_material.id, "collection_index_ids": [collection.id]},
        )

        assert node.llm_provider_id == provider.id
        assert node.source_material_id == source_material.id
        assert list(node.collection_indexes.all()) == [collection]

    def test_a_directly_passed_column_stays_out_of_params(self):
        """The overlay feeding the sync is not persisted."""
        provider = LlmProviderFactory.create()

        node = NodeFactory.create(llm_provider=provider, params={"name": "llm"})

        assert node.llm_provider_id == provider.id
        assert node.params == {"name": "llm"}
        node.refresh_from_db()
        assert node.params == {"name": "llm"}
