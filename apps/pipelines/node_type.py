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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.pipelines.nodes.base import BasePipelineNode


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


def _nodes_base():
    """``apps.pipelines.nodes.base``, imported on use.

    The one lazy import this module makes. At module level it would pull in
    ``nodes -> langgraph -> apps.experiments.models``, which is what stops ``models.py`` and the API
    modules from sharing a single place to ask these questions.
    """
    from apps.pipelines.nodes import base  # noqa: PLC0415 - heavy: nodes→langgraph

    return base
