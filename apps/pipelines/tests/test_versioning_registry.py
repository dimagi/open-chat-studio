import pytest

from apps.experiments.versioning import VersionsMixin
from apps.pipelines.nodes import nodes as pipeline_nodes
from apps.pipelines.versioning import (
    _NODE_PARAM_SPECS,
    ParamArchiving,
    ParamVersioning,
    VersionedParamSpec,
)

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
