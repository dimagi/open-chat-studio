"""Everything a stored ``Node.type`` decides, in one place.

``NodeType`` holds no node state: it is keyed only by the type string, so it is constructible from a
stored row, from an unwritten edit on the wire, or from a bare string. Params are arguments to the
questions that need them rather than fields, so this cannot become a second source of truth for what
a node holds (ADR-0046). The row questions stay on ``Node``.

It is also a null object. ``Node.type`` is graph data with nothing validating it against the node
classes, and ``REMOVED_NODE_TYPES`` makes an unresolvable type a supported state, so every accessor
here answers for one -- empty, ``None`` or ``False`` -- rather than raising.

Deliberately not in ``nodes/base.py``: nothing here imports ``apps.pipelines.nodes`` at module
level, which is what lets ``models.py``, ``flow.py``, ``versioning.py`` and the API modules all ask
these questions without dragging in the ``nodes -> langgraph -> apps.experiments.models`` chain.
"""

from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from apps.pipelines.nodes.base import BasePipelineNode, NodeSchema


@dataclass(frozen=True)
class NodeType:
    """The node type a ``Node.type`` string names, and what it decides."""

    type: str

    @property
    def node_class(self) -> "type[BasePipelineNode] | None":
        """The node class this type names, or ``None`` if it names no usable one."""
        return _nodes_base().resolve_node_class(self.type)

    @property
    def exists(self) -> bool:
        """Whether the type names a usable node class at all."""
        return self.node_class is not None

    @property
    def declared_params(self) -> frozenset[str]:
        """The param names this type declares. An unresolvable type declares none."""
        node_class = self.node_class
        return frozenset(node_class.model_fields) if node_class is not None else frozenset()

    def declares(self, param_name: str) -> bool:
        """Whether this type declares ``param_name`` as a param."""
        return param_name in self.declared_params

    @property
    def schema(self) -> "NodeSchema | None":
        """This type's ``NodeSchema`` -- its display label, and whether it can be added or deleted."""
        node_class = self.node_class
        if node_class is None:
            return None
        # Cast because pydantic types this config key as a plain JSON dict or a callable, while every
        # node class stores a `NodeSchema` in it -- `deprecated_node` reads it back the same way.
        return cast("NodeSchema", node_class.model_config["json_schema_extra"])

    @property
    def is_server_managed(self) -> bool:
        """Whether the server owns nodes of this type -- Start and End, the two the API will not create.

        ``can_delete`` is the UI builder's own flag for this, so callers withhold the same nodes the
        builder does rather than keeping a list of their own. An unresolvable type has no flag to
        consult and reports ``False``: it is exactly the sort of node a pipeline has to be able to shed.
        """
        schema = self.schema
        return schema is not None and not schema.can_delete


@cache
def server_managed_node_types() -> frozenset[str]:
    """Every type :attr:`NodeType.is_server_managed` is true of, for a caller that needs the set in SQL.

    Derived from the same ``can_delete`` flag rather than listed, so the two cannot disagree. Memoised
    because the schemas are static per deploy. A function rather than a module constant: building it
    imports the node classes, which is the import this module exists to keep out of its callers.
    """
    from apps.pipelines.nodes.node_metadata import get_node_schemas  # noqa: PLC0415 - heavy: nodes→langgraph

    return frozenset(schema["title"] for schema in get_node_schemas() if not schema.get("ui:can_delete"))


def _nodes_base():
    """``apps.pipelines.nodes.base``, imported on use.

    The one lazy import this module makes. At module level it would pull in
    ``nodes -> langgraph -> apps.experiments.models``, which is what stops ``models.py`` and the API
    modules from sharing a single place to ask these questions.
    """
    from apps.pipelines.nodes import base  # noqa: PLC0415 - heavy: nodes→langgraph

    return base
