import pytest

from apps.pipelines.nodes.nodes import LLMResponseWithPrompt
from apps.utils.deletion import (
    get_related_experiment_versions_queryset,
    get_related_pipeline_nodes_queryset,
    has_related_pipeline_references,
)
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory


@pytest.mark.django_db()
class TestGetRelatedPipelineNodesQueryset:
    def test_matches_scalar_and_list_params_and_ignores_archived_nodes(self):
        collection = CollectionFactory.create()
        pipeline = PipelineFactory.create()
        scalar_node = NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={"collection_id": str(collection.id)},
        )
        list_node = NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={"collection_index_ids": [str(collection.id)]},
        )
        archived_node = NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={"collection_id": str(collection.id)},
        )
        archived_node.is_archived = True
        archived_node.save(update_fields=["is_archived"])

        result = get_related_pipeline_nodes_queryset(collection, "collection_id", "collection_index_ids")

        assert set(result) == {scalar_node, list_node}

    def test_without_list_param_key_only_checks_the_scalar_param(self):
        collection = CollectionFactory.create()
        pipeline = PipelineFactory.create()
        NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={"collection_index_ids": [str(collection.id)]},
        )

        result = get_related_pipeline_nodes_queryset(collection, "source_material_id")

        assert list(result) == []

    def test_matches_a_node_in_a_non_default_published_experiment_version(self):
        """This is the tier that makes the overall check broader than OpenAiAssistant's own — no
        pipeline/experiment-status filtering at all, so a live node counts regardless of whether its
        pipeline belongs to the current default published experiment or a superseded one."""
        collection = CollectionFactory.create()
        pipeline = PipelineFactory.create()
        node = NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={"collection_id": str(collection.id)},
        )
        experiment = ExperimentFactory.create(pipeline=pipeline)
        published = experiment.create_new_version()
        published.is_default_version = False
        published.save()

        # collection_id is LIVE_REFERENCE, so publishing never rewrites it — the working node still
        # references collection.id verbatim too. Clear it so only the published version's node
        # (found via its matching flow_id) is left referencing the collection, isolating this tier
        # from the working-node reference that would otherwise also match.
        node.params = {}
        node.save()
        published_node = published.pipeline.node_set.get(flow_id=node.flow_id)

        result = get_related_pipeline_nodes_queryset(collection, "collection_id", "collection_index_ids")

        assert list(result) == [published_node]


@pytest.mark.django_db()
class TestGetRelatedExperimentVersionsQueryset:
    def test_matches_default_published_version_referencing_any_version_of_the_instance(self):
        collection = CollectionFactory.create()
        collection_version = collection.create_new_version()
        pipeline = PipelineFactory.create()
        node = NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={"collection_id": str(collection_version.id)},
        )
        experiment = ExperimentFactory.create(pipeline=pipeline)
        published = experiment.create_new_version()

        # collection_id is LIVE_REFERENCE, so publishing never rewrites it — the working
        # experiment's own node still references collection_version.id verbatim too. Clear it so
        # only the published version's node is left referencing it, isolating this tier from the
        # working-experiment reference that would otherwise also match.
        node.params = {}
        node.save()

        result = get_related_experiment_versions_queryset(collection, "collection_id", "collection_index_ids")

        assert list(result) == [published]

    def test_ignores_a_published_version_that_is_not_the_default(self):
        collection = CollectionFactory.create()
        collection_version = collection.create_new_version()
        pipeline = PipelineFactory.create()
        node = NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={"collection_id": str(collection_version.id)},
        )
        experiment = ExperimentFactory.create(pipeline=pipeline)
        published = experiment.create_new_version()
        published.is_default_version = False
        published.save()

        # Clear the working experiment's own leftover reference (see note above) so only the
        # non-default published version's node references the collection, isolating what this
        # test is actually checking.
        node.params = {}
        node.save()

        result = get_related_experiment_versions_queryset(collection, "collection_id", "collection_index_ids")

        assert list(result) == []


@pytest.mark.django_db()
class TestHasRelatedPipelineReferences:
    def test_true_when_a_live_node_references_the_instance(self):
        collection = CollectionFactory.create()
        pipeline = PipelineFactory.create()
        NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={"collection_id": str(collection.id)},
        )

        assert has_related_pipeline_references(collection, "collection_id", "collection_index_ids") is True

    def test_false_when_nothing_references_the_instance(self):
        collection = CollectionFactory.create()

        assert has_related_pipeline_references(collection, "collection_id", "collection_index_ids") is False

    def test_true_when_only_a_published_version_still_needs_it(self):
        collection = CollectionFactory.create()
        collection_version = collection.create_new_version()
        pipeline = PipelineFactory.create()
        NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={"collection_id": str(collection_version.id)},
        )
        experiment = ExperimentFactory.create(pipeline=pipeline)
        experiment.create_new_version()

        assert has_related_pipeline_references(collection, "collection_id", "collection_index_ids") is True

    def test_false_for_a_version_instance_even_if_its_working_record_has_other_versions_in_use(self):
        """The second tier only applies when checking the working instance itself — a version
        instance's own `is_working_version` is False, so only the direct-node tier applies to it."""
        collection = CollectionFactory.create()
        collection_version = collection.create_new_version()
        other_version = collection.create_new_version()
        pipeline = PipelineFactory.create()
        NodeFactory.create(
            type=LLMResponseWithPrompt.__name__,
            pipeline=pipeline,
            params={"collection_id": str(other_version.id)},
        )
        experiment = ExperimentFactory.create(pipeline=pipeline)
        experiment.create_new_version()

        assert has_related_pipeline_references(collection_version, "collection_id", "collection_index_ids") is False
