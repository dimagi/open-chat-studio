"""The pipeline façade's node and edge endpoints (#4140, #4141).

A chatbot's pipeline is edited one node or one edge at a time rather than replaced wholesale: the
server holds the graph and applies the change, so a client never has to reproduce a whole document
to alter one setting. Every write answers with the same envelope -- what it wrote, then the state of
the pipeline it wrote into -- so a client never has to re-read to find out where it has got to.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import APIException
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.api.permissions import BASE_PERMISSION_CLASSES
from apps.api.v2.discovery.node_types import get_node_class
from apps.api.v2.write.base import ChatbotCompositionPermission, DescribesPatch
from apps.oauth.permissions import TokenHasOAuthResourceScope
from apps.pipelines.build_state import pipeline_build_state
from apps.pipelines.flow import FlowEdge
from apps.pipelines.models import Pipeline

from . import edge_editor
from .examples import create_examples, update_examples
from .facade import edit_pipeline
from .graph_editor import plan_create, plan_delete, plan_update
from .node_params import writable_params
from .references import check_references
from .serializers import (
    MAX_WIRES_PER_CALL,
    WIRES_FIELD,
    EdgeWriteSerializer,
    LeadsWithWhatWasWritten,
    NodeCreateSerializer,
    NodeUpdateSerializer,
    NodeWriteSerializer,
    PipelineWriteSerializer,
    WireBodySerializer,
    WrittenEdgeSerializer,
    WrittenNodeSerializer,
)

CHATBOT_ID = OpenApiParameter(
    name="id", type=OpenApiTypes.UUID, location=OpenApiParameter.PATH, description="Chatbot ID"
)
NODE_ID = OpenApiParameter(
    name="node_id",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    description="The node's server-assigned id, as returned by a write or by the `chatbot_inspect` endpoint.",
)
EDGE_ID = OpenApiParameter(
    name="edge_id",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    description="The edge's server-assigned id, as returned by a wire or by the `chatbot_inspect` endpoint.",
)
BAD_REQUEST = OpenApiResponse(
    description=(
        "The body is not one this endpoint can act on. Errors are keyed by the field at fault, with "
        "param-level errors nested under `params`: an unrecognised body key, a server-assigned key "
        "the client tried to set (`node_id`, `position`), or a param naming a resource this team "
        "cannot reach.\n\n"
        "A param the type does not declare is dropped rather than refused, and a param whose value "
        "the type cannot parse is stored and reported in `pipeline_errors` — so a node can be built "
        "up over several calls."
    )
)
FORBIDDEN = OpenApiResponse(
    description=(
        "The caller is authenticated but not authorised to modify this chatbot: either its role "
        "lacks permission to change chatbots, or it is a machine (client-credentials) token whose "
        "application is not authorised for this chatbot."
    )
)
SERVER_MANAGED = OpenApiResponse(
    description=(
        "The node is part of the pipeline's structure (the Start or End node) and cannot be edited "
        "or deleted through the API."
    )
)
#: The shapes a wire body comes in. Node ids are placeholders for ones a node write or ``/inspect/``
#: returned.
WIRE_EXAMPLES = [
    OpenApiExample(
        name="Plain node",
        summary="A source with one output handle: neither handle need be named.",
        value={WIRES_FIELD: [{"source": "CodeNode-a1b2c", "target": "LLMResponseWithPrompt-d3e4f"}]},
        request_only=True,
    ),
    OpenApiExample(
        name="Router branch",
        summary="A router exposes one handle per keyword, so the branch to wire has to be named.",
        value={
            WIRES_FIELD: [
                {
                    "source": "RouterNode-9f8e7",
                    "target": "LLMResponseWithPrompt-d3e4f",
                    "source_handle": "output_1",
                }
            ]
        },
        request_only=True,
    ),
    OpenApiExample(
        name="A whole branch",
        summary="Several wires in one call, so a router and both its branches land together.",
        value={
            WIRES_FIELD: [
                {"source": "RouterNode-9f8e7", "target": "LLMResponseWithPrompt-d3e4f", "source_handle": "output_0"},
                {"source": "RouterNode-9f8e7", "target": "CodeNode-a1b2c", "source_handle": "output_1"},
                {"source": "CodeNode-a1b2c", "target": "EndNode-b2c3d"},
            ]
        },
        request_only=True,
    ),
]


class PipelineFacadeView(GenericAPIView):
    """What every pipeline façade endpoint shares: the gates, and the shape of the response.

    Declared once because these are the *same* gates, not merely similar ones. Editing a chatbot's
    composition is a *change* to the chatbot whatever the verb, so the stock
    ``DjangoModelPermissions`` verb map is replaced rather than extended: deleting a pipeline node
    is not deleting the chatbot.
    """

    permission_classes = [*BASE_PERMISSION_CLASSES, ChatbotCompositionPermission, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]
    # Only here so the generic view has a queryset; permissions are not derived from it.
    queryset = Pipeline.objects.none()

    #: The response serializer for this resource. Its ``written_field`` names the key the written
    #: resource goes under, so the body and the published schema cannot disagree about it.
    write_response_serializer: type[LeadsWithWhatWasWritten]

    def _edit(self, request, chatbot_id: str, plan) -> dict:
        """Run one façade edit and build its response. ``plan`` is all that differs per endpoint."""
        return edit_pipeline(request, chatbot_id, plan, self._write_response)

    def _write_response(self, pipeline: Pipeline, written_ids: list[str]) -> dict:
        """The pipeline's build state, and in front of it whatever this write created or changed.

        ``written_ids`` is empty for a delete -- there is nothing left to describe -- so the
        response is the pipeline's state alone.
        """
        body = pipeline_state(pipeline)
        if not written_ids:
            return body
        shape = self.write_response_serializer
        written = []
        for written_id in written_ids:
            record = self._written(pipeline, written_id)
            if record is None:
                # Always a bug in this package rather than anything the client did -- the patch
                # engine applied the diff, so the thing is there. *Which* bug differs per resource,
                # and each `_written` says which one it is guarding against.
                raise APIException(f"'{written_id}' was written but could not be read back.")
            written.append(record)
        return {shape.written_field: written if shape.written_many else written[0], **body}

    def _written(self, pipeline: Pipeline, written_id: str) -> dict | None:
        """This resource as the response reports it, or ``None`` if the write cannot be read back."""
        raise NotImplementedError


class PipelineNodeEditView(PipelineFacadeView):
    """The façade's node endpoints: add a node, edit one, remove one.

    Serves both routes, so each ``path()`` narrows ``http_method_names`` to the verbs it offers --
    otherwise a PATCH to the collection route would reach ``patch`` with no ``node_id`` and raise
    instead of answering 405.
    """

    write_response_serializer = NodeWriteSerializer
    # So that OPTIONS on the detail route describes its PATCH body, which stock DRF metadata leaves
    # out entirely.
    metadata_class = DescribesPatch

    def get_serializer_class(self) -> type[serializers.Serializer]:
        """POST takes a `type`, PATCH takes params -- keyed on the method since one class serves both.

        `DescribesPatch` calls this once per verb it describes, via a `clone_request`, so OPTIONS
        still resolves the right body for each.
        """
        return NodeCreateSerializer if self.request.method == "POST" else NodeUpdateSerializer

    @extend_schema(
        operation_id="pipeline_node_create",
        summary="Add a Pipeline Node",
        description=(
            "Add a node to the chatbot's working (draft) pipeline.\n\n"
            "`type` alone is enough: the node is created with that type's defaults, which the "
            "response reports and which you can then change with PATCH. It is not wired to "
            "anything, so it appears in `unwired_handles` until you connect it — that is advisory, "
            "not an error.\n\n"
            "The node's `node_id` and its position on the canvas are assigned by the server and "
            "cannot be chosen.\n\n"
            "What `params` may hold depends on `type`, so the examples below show a full body for "
            "each type. The `pipeline_node_retrieve` endpoint is the authoritative JSON Schema for "
            "one type, and the `pipeline_node_options` endpoint serves the ids its resource params "
            "may name."
        ),
        tags=["Pipelines"],
        parameters=[CHATBOT_ID],
        request=NodeCreateSerializer,
        examples=create_examples(),
        responses={
            201: NodeWriteSerializer,
            400: BAD_REQUEST,
            403: FORBIDDEN,
            404: OpenApiResponse(
                description=(
                    "No such chatbot, or no such node `type`. An unknown type names the valid ones "
                    "in `valid_types`; the Start and End types are refused here too, since the "
                    "server creates those with the pipeline."
                )
            ),
        },
    )
    def post(self, request, id: str) -> Response:
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        label = body.validated_data.get("label")
        node_type = body.validated_data["type"]
        node_class = get_node_class(node_type)
        params = writable_params(node_class, body.validated_data["params"])
        check_references(team=request.team, node_class=node_class, params=params)
        return Response(
            self._edit(request, id, lambda flow: plan_create(flow, node_type, label, params)),
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        operation_id="pipeline_node_update",
        summary="Edit a Pipeline Node",
        description=(
            "Change a node's params or its label. Params merge key by key, so send only what you "
            "want to change; everything else is left as it is.\n\n"
            "Editing a router's `keywords` regenerates its output handles — they are positional, so "
            "`output_0` serves `keywords[0]` — and the response carries the new list. The node's "
            "edges follow their keyword: dropping a keyword deletes the edge that served it, and a "
            "renamed keyword counts as one branch gone and another new. Handle names are not stable "
            "across a keyword edit, so re-read `output_handles` after one.\n\n"
            "What `params` may hold depends on the node's type, not on the verb: the `POST` "
            "examples name every param of each type, and the examples here are partial bodies "
            "instead. The `pipeline_node_retrieve` endpoint is the authoritative JSON Schema for "
            "one type, and the `pipeline_node_options` endpoint serves the ids its resource params "
            "may name."
        ),
        tags=["Pipelines"],
        parameters=[CHATBOT_ID, NODE_ID],
        request=NodeUpdateSerializer,
        examples=update_examples(),
        responses={
            200: NodeWriteSerializer,
            400: BAD_REQUEST,
            403: FORBIDDEN,
            404: OpenApiResponse(
                description=(
                    "No such chatbot or node, or the node is of a type this API does not publish "
                    "and so cannot describe the params of."
                )
            ),
            409: SERVER_MANAGED,
        },
    )
    def patch(self, request, id: str, node_id: str) -> Response:
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        params = body.validated_data["params"]
        label = body.validated_data.get("label")
        return Response(
            self._edit(
                request,
                id,
                # The node's type comes from the graph, so its params can only be checked under the
                # lock; the team goes in so the check can look its references up there.
                lambda flow: plan_update(flow, request.team, node_id, label, params),
            )
        )

    @extend_schema(
        operation_id="pipeline_node_delete",
        summary="Remove a Pipeline Node",
        description=(
            "Remove a node from the chatbot's working (draft) pipeline, along with every edge that "
            "referenced it — you do not have to unwire it first.\n\n"
            "The hole this leaves is reported rather than refused: unsplicing a node usually breaks "
            "the path to the End node, which comes back in `pipeline_errors` for you to repair.\n\n"
            "The Start and End nodes cannot be removed — nor edited. They are part of the "
            "pipeline's structure and cannot be added back through the API."
        ),
        tags=["Pipelines"],
        parameters=[CHATBOT_ID, NODE_ID],
        request=None,
        responses={
            200: PipelineWriteSerializer,
            403: FORBIDDEN,
            404: OpenApiResponse(description="No such chatbot or node."),
            409: OpenApiResponse(description="The node is part of the pipeline's structure and cannot be deleted."),
        },
    )
    def delete(self, request, id: str, node_id: str) -> Response:
        # `plan_delete` names no node, so the response reports the pipeline alone.
        return Response(self._edit(request, id, lambda flow: plan_delete(flow, node_id)))

    def _written(self, pipeline: Pipeline, written_id: str) -> dict | None:
        """Read from the reconciled rows: a miss means the graph and the rows disagree."""
        node = next((node for node in pipeline.node_set.all() if node.flow_id == written_id), None)
        return None if node is None else WrittenNodeSerializer(node).data


class PipelineEdgeEditView(PipelineFacadeView):
    """The façade's edge endpoints: wire nodes together, unwire them one at a time.

    An edge is not editable in place -- there is nothing to change but its two ends, and moving one
    is a different wire -- so repointing a branch is a delete followed by a wire. That is also why
    the detail route offers DELETE alone, and why stock DRF metadata suffices here where the node
    routes need ``DescribesPatch``.
    """

    write_response_serializer = EdgeWriteSerializer
    serializer_class = WireBodySerializer

    @extend_schema(
        operation_id="pipeline_edge_create",
        summary="Wire Pipeline Nodes",
        description=(
            "Add edges to the chatbot's working (draft) pipeline, wiring one node's output handle to "
            "another node's input.\n\n"
            f"The wires go in a **list** under `{WIRES_FIELD}` — a single wire is a list of one, and "
            f"at most {MAX_WIRES_PER_CALL} go in one call — and a call carrying several is all or "
            "nothing: if any one of them is refused, none of them are stored and the pipeline is "
            "left exactly as the call found it. So a whole branch lands in one call with no "
            "half-wired state to unpick:\n\n"
            "```json\n"
            "{\n"
            f'  "{WIRES_FIELD}": [\n'
            '    {"source": "RouterNode-9f8e7", "target": "LLMResponseWithPrompt-d3e4f", '
            '"source_handle": "output_0"},\n'
            '    {"source": "RouterNode-9f8e7", "target": "CodeNode-a1b2c", "source_handle": "output_1"},\n'
            '    {"source": "CodeNode-a1b2c", "target": "EndNode-b2c3d"}\n'
            "  ]\n"
            "}\n"
            "```\n\n"
            "`source` and `target` are enough for most nodes: a handle you leave out means the only "
            "one the node has. A router is the exception — it exposes one handle per branch, so name "
            "the branch to wire in `source_handle`. No node is moved on the canvas.\n\n"
            "Each edge's `id` is assigned by the server and cannot be chosen. It is what the "
            "`pipeline_edge_delete` endpoint takes, and `edges` reports them in the order the body "
            "named them.\n\n"
            "Wiring a pair that is already wired the same way is refused rather than stored twice, "
            "so a wire is safe to retry. A wire that leaves the *graph* wrong is not refused: it "
            "lands, and comes back in `pipeline_errors` for you to repair."
        ),
        tags=["Pipelines"],
        parameters=[CHATBOT_ID],
        request=WireBodySerializer,
        examples=WIRE_EXAMPLES,
        responses={
            201: EdgeWriteSerializer,
            400: OpenApiResponse(
                description=(
                    "Nothing was wired. Refusals come back under "
                    f"`{WIRES_FIELD}`, **keyed by the position in the list of the wire at fault** — "
                    "so one call reports everything wrong with it, and a wire that is fine is absent "
                    "rather than empty.\n\n"
                    "Each wire's entry is in turn keyed by the field at fault: a `source` or "
                    "`target` that is not a node in this pipeline, a `source_handle` the source node "
                    "does not offer (or a missing one where the node offers a choice), a "
                    "`target_handle` that is not the target's input, a server-assigned key the "
                    "client tried to set (`id`), or an unrecognised key on the wire.\n\n"
                    "```json\n"
                    "{\n"
                    f'  "{WIRES_FIELD}": {{\n'
                    '    "0": {"source": ["This pipeline has no node \'CodeNode-nope1\'."]},\n'
                    '    "2": {"source_handle": ["..."]}\n'
                    "  }\n"
                    "}\n"
                    "```\n\n"
                    "A duplicate edge is reported under the wire's `non_field_errors`, with the "
                    "existing edge's id between single quotes:\n\n"
                    "> These nodes are already wired this way, by edge '&lt;edge_id&gt;'. Nothing was "
                    "changed.\n\n"
                    "So a client whose first attempt's response it never saw can recover the edge id "
                    "rather than re-reading the pipeline. A body wiring the same pair twice is "
                    "refused the same way, naming the index of the first of the two.\n\n"
                    "A `source` that offers no output handles at all is refused under `source`, and "
                    "the message says whether that is fixable: a router with no `keywords` set yet "
                    "needs a PATCH first, whereas the End node can never be a source.\n\n"
                    f"A `{WIRES_FIELD}` that is not a list, is an empty one, or names more than "
                    f"{MAX_WIRES_PER_CALL} wires is refused as a message on the field itself, before "
                    "any wire in it is looked at; a body that is not an object at all is refused "
                    "under `non_field_errors`."
                )
            ),
            403: FORBIDDEN,
            404: OpenApiResponse(description="No such chatbot."),
        },
    )
    def post(self, request, id: str) -> Response:
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        wires = body.validated_data[WIRES_FIELD]
        return Response(
            self._edit(
                request,
                id,
                # The handles a node offers depend on its params, so the whole body can only be
                # checked against the locked graph.
                lambda flow: edge_editor.plan_create(flow, wires),
            ),
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        operation_id="pipeline_edge_delete",
        summary="Unwire two Pipeline Nodes",
        description=(
            "Remove an edge from the chatbot's working (draft) pipeline. Both nodes stay, and stay "
            "where they are on the canvas.\n\n"
            "The hole this leaves is reported rather than refused: unwiring usually breaks the path "
            "to the End node, which comes back under `pipeline_errors.node[<end node id>].root` for "
            "you to repair, and both ends of the edge reappear in `unwired_handles`.\n\n"
            "Repeating a delete answers 404, so a retry cannot disturb the graph the first call left."
        ),
        tags=["Pipelines"],
        parameters=[CHATBOT_ID, EDGE_ID],
        request=None,
        responses={
            200: PipelineWriteSerializer,
            403: FORBIDDEN,
            404: OpenApiResponse(description="No such chatbot, or no such edge in this chatbot's pipeline."),
        },
    )
    def delete(self, request, id: str, edge_id: str) -> Response:
        # `plan_delete` names no edge, so the response reports the pipeline alone.
        return Response(self._edit(request, id, lambda flow: edge_editor.plan_delete(flow, edge_id)))

    def _written(self, pipeline: Pipeline, written_id: str) -> dict | None:
        """Read from the saved graph rather than reported from the plan: the patch engine skips an
        add whose id is already there, so reporting the planned edge would answer 201 for a wire
        that was silently dropped."""
        stored = next((edge for edge in (pipeline.data or {}).get("edges", []) if edge["id"] == written_id), None)
        return None if stored is None else WrittenEdgeSerializer(FlowEdge(**stored)).data


def pipeline_state(pipeline: Pipeline) -> dict:
    """The three fields every façade write reports about the pipeline it has just changed."""
    state = pipeline_build_state(pipeline)
    return {
        "pipeline_valid": state["pipeline_valid"],
        "pipeline_errors": state["errors"],
        "unwired_handles": state["unwired_handles"],
    }
