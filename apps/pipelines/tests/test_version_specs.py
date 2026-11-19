"""The pipeline and node specs are clean under the checks, and the node's param references map both ways."""

import pytest

from apps.experiments.checks import check_spec
from apps.experiments.versioning import ParamReference, Strategy, VersionsMixin
from apps.pipelines.node_type import node_types_declaring
from apps.pipelines.versioning import NodeVersionSpec, PipelineVersionSpec
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import SourceMaterialFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory

PARAM_REFERENCES = [pytest.param(member, id=member.key) for member in NodeVersionSpec.param_references()]
BY_KEY = {member.key: member for member in NodeVersionSpec.param_references()}

# Members whose model has a get_related_nodes_queryset guard.
GUARD_FACTORIES = {"documents.Collection": CollectionFactory, "experiments.SourceMaterial": SourceMaterialFactory}
GUARD_REFERENCES = [
    pytest.param(member, id=f"{member.model_label}.{member.key}")
    for member in NodeVersionSpec.param_references()
    if hasattr(member.model, "get_related_nodes_queryset")
]

assert PARAM_REFERENCES, "the node spec must declare its param references"
assert GUARD_REFERENCES, "expected at least one guard-participating reference"


@pytest.mark.parametrize("spec_cls", [PipelineVersionSpec, NodeVersionSpec], ids=["pipeline", "node"])
def test_the_spec_is_clean(spec_cls):
    assert check_spec(spec_cls) == []


@pytest.mark.parametrize("member", PARAM_REFERENCES)
def test_each_param_reference_names_a_param_some_node_type_declares(member):
    """Guards against drift between the spec and the node classes: a reference nobody declares does nothing."""
    assert node_types_declaring(member.key), f"no node type declares {member.key!r}"
    assert issubclass(member.model, VersionsMixin)


@pytest.mark.django_db()
def test_map_back_reads_the_versioned_record_off_the_mirror_column():
    source_material = SourceMaterialFactory.create()
    version = source_material.create_new_version()
    node = NodeFactory.create(type="LLMResponseWithPrompt", params={"source_material_id": str(version.id)})
    node.update_from_params()
    assert node.source_material_id == version.id

    params = {"source_material_id": str(version.id)}
    BY_KEY["source_material_id"].map_back(node, params)

    assert params["source_material_id"] == str(source_material.id)


@pytest.mark.django_db()
def test_map_back_leaves_a_live_reference_verbatim():
    collection = CollectionFactory.create()
    member = BY_KEY["collection_id"]
    assert member.strategy is Strategy.LIVE_REFERENCE

    params = {"collection_id": str(collection.id)}
    member.map_back(None, params)

    assert params["collection_id"] == str(collection.id)


@pytest.mark.django_db()
@pytest.mark.parametrize("member", GUARD_REFERENCES)
def test_in_use_guard_param_key_matches_the_spec(member):
    """The guard's hardcoded param key has to match the spec's."""
    instance = GUARD_FACTORIES[member.model_label].create()
    value = [str(instance.id)] if member.many else str(instance.id)
    NodeFactory.create(pipeline=PipelineFactory.create(team=instance.team), params={member.key: value})

    assert instance.get_related_nodes_queryset().exists()


@pytest.mark.django_db()
def test_a_param_a_node_type_does_not_declare_travels_verbatim():
    """A removed or changed type can leave a versioned id in params; it is neither pinned nor archived."""
    source_material = SourceMaterialFactory.create()
    node = NodeFactory.create(type="RenderTemplate", params={"source_material_id": str(source_material.id)})
    llm_node = NodeFactory.create(type="LLMResponseWithPrompt", pipeline=node.pipeline)

    version = NodeVersionSpec().publish(node, pipeline=node.pipeline)

    assert version.params["source_material_id"] == str(source_material.id)
    assert not source_material.versions.exists()
    spec = NodeVersionSpec()
    assert [m.key for m in spec.members_for(node) if isinstance(m, ParamReference)] == []
    assert "source_material_id" in {m.key for m in spec.members_for(llm_node) if isinstance(m, ParamReference)}
