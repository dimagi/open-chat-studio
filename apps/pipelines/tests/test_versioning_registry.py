import pytest

from apps.experiments.versioning import VersionsMixin
from apps.pipelines.nodes import nodes as pipeline_nodes
from apps.pipelines.versioning import (
    _NODE_PARAM_SPECS,
    ParamArchiving,
    ParamVersioning,
    VersionedParamSpec,
)
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import SourceMaterialFactory

ALL_SPECS = [
    pytest.param(node_type, spec, id=f"{node_type}.{spec.param_name}")
    for node_type, specs in _NODE_PARAM_SPECS.items()
    for spec in specs
]

assert ALL_SPECS, "Versioned param registry must not be empty"


@pytest.mark.parametrize(("node_type", "spec"), ALL_SPECS)
def test_registry_node_types_and_params_exist(node_type, spec):
    """Guards against registry drift: every spec must match a real param on a real node class."""
    node_class = getattr(pipeline_nodes, node_type, None)
    assert node_class is not None, f"Unknown node type '{node_type}' in versioned param registry"
    assert spec.param_name in node_class.model_fields, (
        f"'{spec.param_name}' is not a param of {node_type}; update the registry in apps.pipelines.versioning"
    )


@pytest.mark.parametrize(("node_type", "spec"), ALL_SPECS)
def test_registry_models_are_versioned(node_type, spec):
    model_cls = spec.model_cls
    assert issubclass(model_cls, VersionsMixin), f"{spec.model_label} does not support versioning"


@pytest.mark.django_db()
def test_revert_referenced_record_maps_version_id_to_working_id():
    """revert_referenced_record is the inverse of version_referenced_record: a param holding a
    versioned record id is rewritten back to the working version's id."""
    source_material = SourceMaterialFactory.create()
    version = source_material.create_new_version()
    spec = _NODE_PARAM_SPECS["LLMResponseWithPrompt"][0]
    assert spec.param_name == "source_material_id"

    params = {"source_material_id": str(version.id)}
    spec.revert_referenced_record(params)
    assert params["source_material_id"] == str(source_material.id)


@pytest.mark.django_db()
def test_revert_referenced_record_leaves_live_reference_verbatim():
    """LIVE_REFERENCE params were never rewritten on publish, so revert must leave them as-is."""
    collection = CollectionFactory.create()
    specs = {spec.param_name: spec for spec in _NODE_PARAM_SPECS["LLMResponseWithPrompt"]}
    spec = specs["collection_id"]
    assert spec.versioning == ParamVersioning.LIVE_REFERENCE

    params = {"collection_id": str(collection.id)}
    spec.revert_referenced_record(params)
    assert params["collection_id"] == str(collection.id)


def test_versioning_multi_id_params_is_unsupported():
    with pytest.raises(ValueError, match="multi-ID"):
        VersionedParamSpec(
            param_name="some_ids",
            model_label="documents.Collection",
            display_name="some",
            versioning=ParamVersioning.NEW_VERSION,
            archiving=ParamArchiving.KEEP,
            many=True,
        )
