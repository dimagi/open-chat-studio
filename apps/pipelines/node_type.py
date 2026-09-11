"""Everything a stored ``Node.type`` decides, in one place.

``NodeType`` holds no node state: it is keyed only by the type string, so it is constructible from a
stored row, from an unwritten edit on the wire, or from a bare string. Params are arguments to the
questions that need them rather than fields, so this cannot become a second source of truth for what
a node holds (ADR-0046). The row questions stay on ``Node``.

It is also a null object. ``Node.type`` is graph data with nothing validating it against the node
classes, and ``REMOVED_NODE_TYPES`` makes an unresolvable type a supported state, so every accessor
here answers for one -- empty, ``None`` or ``False`` -- rather than raising. ``exists`` is the
explicit question for a caller that has to tell the cases apart.

Deliberately not in ``nodes/base.py``: nothing here imports ``apps.pipelines.nodes`` at module
level, which is what lets ``models.py``, ``flow.py``, ``versioning.py`` and the API modules all ask
these questions without dragging in the ``nodes -> langgraph -> apps.experiments.models`` chain.
"""

from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import TYPE_CHECKING, cast

import pydantic

from apps.pipelines.const import (
    END_NODE_TYPE,
    REACT_FLOW_END_TYPE,
    REACT_FLOW_NODE_TYPE,
    REACT_FLOW_START_TYPE,
    STANDARD_INPUT_NAME,
    STANDARD_OUTPUT_NAME,
    START_NODE_TYPE,
)
from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.pipelines.versioning import NODE_PARAM_SPECS, VersionedParamSpec

if TYPE_CHECKING:
    from apps.pipelines.models import Node
    from apps.pipelines.nodes.base import BasePipelineNode, NodeSchema


class NoOutputHandles(StrEnum):
    """Why a node offers none, for a caller that has to explain an empty ``output_handles``.

    Beside ``NodeType.output_handles`` because it reads that method's branches a second way: kept
    apart, the two drift. Hence ``UNDETERMINED`` rather than a fall-through to ``TERMINAL``.
    """

    #: The End node. Nothing runs after the end of the pipeline, so nothing can be wired from it.
    TERMINAL = "terminal"
    #: A type naming no node class -- removed since, or never one. Its handles are unknowable.
    UNKNOWN_TYPE = "unknown_type"
    #: A router with no keywords yet: its handles *are* its branches, so it has none until they are set.
    NO_BRANCHES = "no_branches"
    #: Offers none for a reason this method does not recognise -- unreachable today.
    UNDETERMINED = "undetermined"


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

    @property
    def react_flow_type(self) -> str:
        """This type's react-flow node type, which is what the editor renders it as."""
        if self.type == START_NODE_TYPE:
            return REACT_FLOW_START_TYPE
        if self.type == END_NODE_TYPE:
            return REACT_FLOW_END_TYPE
        return REACT_FLOW_NODE_TYPE

    @property
    def render_order(self) -> int:
        """Sort key that puts the start node first and the end node last, leaving the rest in order."""
        return {START_NODE_TYPE: 0, END_NODE_TYPE: 2}.get(self.type, 1)

    @property
    def versioned_param_specs(self) -> tuple[VersionedParamSpec, ...]:
        """The params of this type that reference database records (``apps.pipelines.versioning``)."""
        return NODE_PARAM_SPECS.get(self.type, ())

    def input_handles(self) -> list[str]:
        """The input handles a node of this type accepts an edge on.

        Every type has one implicit ``input`` handle -- bar Start, which has none. A list rather than a
        flag so a caller reads inputs and outputs the same way.
        """
        return [] if self.type == START_NODE_TYPE else [STANDARD_INPUT_NAME]

    def output_handles(self, params: dict, node_id: str, django_node: "Node | None" = None) -> list[dict]:
        """The output handles a node of this type and these params offers, as ``{handle, label}``.

        Routers get one handle per branch from ``get_output_map()`` (``output_0``, ``output_1``, …,
        labelled with the branch keyword); plain nodes get the single standard output with no label;
        End has no outputs.

        Takes the params rather than a stored :class:`~apps.pipelines.models.Node` so a caller holding
        an unwritten edit can ask what the node *would* offer. ``django_node`` is what the row-backed
        caller passes for full validation; without it a router falls back to the unvalidated path
        below, which is enough because no router's branches depend on its row.
        """
        if self.type == END_NODE_TYPE:
            return []
        node_class = self.node_class
        if node_class is None:
            # A type naming no node class (removed since, or never one): validation reports it; we can't
            # know its handles.
            return []
        if issubclass(node_class, _nodes_base().PipelineRouterNode):
            output_map = _router_output_map(node_class, params, node_id, django_node)
            return [{"handle": handle, "label": label} for handle, label in output_map.items()]
        return [{"handle": STANDARD_OUTPUT_NAME, "label": None}]

    def why_no_output_handles(self) -> NoOutputHandles:
        """Which of the empty cases applies. Only meaningful once :meth:`output_handles` returned ``[]``.

        Mirrors that method's branches in the same order, so the two are read together when a case is
        added to either.
        """
        if self.type == END_NODE_TYPE:
            return NoOutputHandles.TERMINAL
        node_class = self.node_class
        if node_class is None:
            return NoOutputHandles.UNKNOWN_TYPE
        if issubclass(node_class, _nodes_base().PipelineRouterNode):
            return NoOutputHandles.NO_BRANCHES
        return NoOutputHandles.UNDETERMINED


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


def _router_output_map(
    node_class: "type[BasePipelineNode]", params: dict, node_id: str, django_node: "Node | None"
) -> dict:
    """A router's handle -> branch-label map, tolerant of invalid params.

    Prefer full validation so every field normalization applies — a router type whose
    ``get_output_map()`` depends on validated/derived fields stays correct at the cost of one
    redundant validation per read. An incrementally-built router can be invalid in ways unrelated
    to its branches (a missing required field, a broken resource reference raising
    ``PipelineNodeBuildError``), and must still report its handles, so fall back to an unvalidated
    instance with the keywords upper-cased to match ``RouterMixin.ensure_keywords_are_uppercase``.
    """
    try:
        instance = node_class.model_validate({**params, "node_id": node_id, "django_node": django_node})
    except (pydantic.ValidationError, PipelineNodeBuildError):
        fallback = dict(params)
        if isinstance(fallback.get("keywords"), list):
            fallback["keywords"] = [str(keyword).upper() for keyword in fallback["keywords"]]
        instance = node_class.model_construct(**fallback)
    return instance.get_output_map()
