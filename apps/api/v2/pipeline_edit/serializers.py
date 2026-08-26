"""Request and response shapes for the pipeline façade (#4140)."""

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.api.v2.inspect.serializers import OutputHandleSerializer, PipelineBuildErrorsSerializer
from apps.api.v2.write.base import RejectsUnknownKeys
from apps.pipelines.build_state import node_output_handles
from apps.pipelines.models import Node

from .graph_editor import settable_params

#: Where a param's name comes from, which is the one thing a free-form ``params`` object cannot say
#: for itself. Spelled out on both write bodies, since a client reads one or the other.
PARAM_NAMES = (
    "Param names are the node type's own, as published by "
    "`GET /api/v2/pipeline/nodes/{node_type}/`. They are not always the name of the "
    "`/pipeline/options/` list a param draws its values from -- `source_material_id` is the param, "
    "`source_material` is the list it chooses from -- so send the param name. A name the type does "
    "not declare is dropped rather than refused, and the response reports the params the node "
    "actually ended up holding."
)

#: Keys a client might reasonably try to set that the server owns, and why it does (W5). Called out
#: by name because the generic "unrecognised field" answer reads as a typo rather than as a rule.
SERVER_ASSIGNED_KEYS = {
    "node_id": "Node ids are assigned by the server and returned in the response; they cannot be chosen.",
    "position": "Node positions are assigned by the server; move a node in the pipeline builder instead.",
}


class RejectsServerAssignedKeys:
    """Answer a client-supplied server-owned key with the rule, not with 'no such field'."""

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, dict):
            claimed = {key: reason for key, reason in SERVER_ASSIGNED_KEYS.items() if key in data}
            if claimed:
                raise serializers.ValidationError(claimed)
        return super().to_internal_value(data)


class NodeCreateSerializer(RejectsServerAssignedKeys, RejectsUnknownKeys, serializers.Serializer):
    """The POST body: a node type, and what to start it off with.

    ``type`` alone is enough — the server fills in the type's defaults, and whatever is still
    missing is reported rather than refused, so a node can be built up over several calls.
    """

    type = serializers.CharField(help_text="A node type from GET /api/v2/pipeline/nodes/.")
    label = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Display name shown in the pipeline builder. Defaults to the node type's own label.",
    )
    params = serializers.DictField(
        required=False,
        default=dict,
        help_text=(
            "The node's configuration. Anything omitted takes the type's default, so `type` alone "
            "is a valid body. " + PARAM_NAMES
        ),
    )


class WrittenNodeSerializer(serializers.Serializer):
    """A node as a write returns it.

    Deliberately not the inspect node shape: inspect keeps resource ids out of ``params`` and
    renders the resolved resources instead, whereas this is the shape you can send back.
    """

    node_id = serializers.CharField(source="flow_id")
    type = serializers.CharField()
    label = serializers.CharField()
    params = serializers.SerializerMethodField()
    output_handles = serializers.SerializerMethodField()

    @extend_schema_field(serializers.DictField())
    def get_params(self, node: Node) -> dict:
        # Narrowed to what a client may send back: a node is stored with a default for every field
        # its type declares, including the ones the API withholds from the schema, and reporting
        # one of those would make this response a body PATCH refuses.
        return settable_params(node)

    @extend_schema_field(OutputHandleSerializer(many=True))
    def get_output_handles(self, node: Node) -> list:
        # Server-derived (W5): a router gets one handle per branch keyword, a plain node the single
        # standard output. Returned on every write so the next call can wire an edge from it
        # without a re-read.
        return node_output_handles(node)


class NodeUpdateSerializer(RejectsServerAssignedKeys, RejectsUnknownKeys, serializers.Serializer):
    """The PATCH body: the params and label to change, and nothing else.

    ``type`` is absent on purpose — a node's type decides what its params mean, so changing it in
    place would reinterpret every stored value. Delete the node and add one of the other type.
    """

    label = serializers.CharField(
        required=False, allow_blank=True, help_text="Display name shown in the pipeline builder."
    )
    params = serializers.DictField(
        required=False,
        default=dict,
        help_text="The params to change; the ones you leave out are left as they are. " + PARAM_NAMES,
    )


class PipelineWriteSerializer(serializers.Serializer):
    """What every façade write reports back about the pipeline it just changed.

    The same three fields ``GET /chatbots/{id}/inspect/`` publishes, so one shape is parsed across
    read and write. ``pipeline_errors`` is what the publish gate rejects on; ``unwired_handles`` is
    advisory and never blocks anything.
    """

    pipeline_valid = serializers.BooleanField(
        help_text="Whether the pipeline validates cleanly: all three error buckets empty, and nothing more."
    )
    pipeline_errors = PipelineBuildErrorsSerializer()
    unwired_handles = serializers.DictField(
        child=serializers.ListField(child=OutputHandleSerializer()),
        help_text=(
            "Advisory 'what still needs wiring' map, keyed by ``node_id``: every output handle with "
            "no outgoing edge and every implicit ``input`` handle with no incoming edge. Never an "
            "error and never blocks a publish."
        ),
    )


class NodeWriteSerializer(PipelineWriteSerializer):
    """A node write's response: the node as written, then the pipeline's state after the write."""

    node = WrittenNodeSerializer()

    def get_fields(self) -> dict[str, serializers.Field]:
        # `node` first: it is what the caller asked about, and the build state is the context.
        fields = super().get_fields()
        return {"node": fields.pop("node"), **fields}
