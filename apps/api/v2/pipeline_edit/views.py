"""The pipeline façade's node endpoints (#4140).

A chatbot's pipeline is edited one node at a time rather than replaced wholesale: the server holds
the graph and applies the change, so a client never has to reproduce a whole document to alter one
setting, and two edits to different nodes cannot overwrite each other.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import APIException
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.api.permissions import BASE_PERMISSION_CLASSES
from apps.api.v2.discovery.node_types import get_node_class
from apps.api.v2.write.base import ChatbotCompositionPermission, DescribesPatch
from apps.oauth.permissions import TokenHasOAuthResourceScope
from apps.pipelines.build_state import pipeline_build_state
from apps.pipelines.models import Pipeline

from .facade import edit_pipeline
from .graph_editor import plan_create, plan_delete, plan_update
from .node_params import writable_params
from .references import check_references
from .serializers import (
    NodeCreateSerializer,
    NodeUpdateSerializer,
    NodeWriteSerializer,
    PipelineWriteSerializer,
    WrittenNodeSerializer,
)

CHATBOT_ID = OpenApiParameter(
    name="id", type=OpenApiTypes.UUID, location=OpenApiParameter.PATH, description="Chatbot ID"
)
NODE_ID = OpenApiParameter(
    name="node_id",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    description="The node's server-assigned id, as returned by a write or by GET /api/v2/chatbots/{id}/inspect/.",
)
BAD_REQUEST = OpenApiResponse(
    description=(
        "The body is not one this endpoint can act on. Errors are keyed by the field at fault, and "
        "param-level errors are nested under `params`. This covers an unrecognised body key, a "
        "server-assigned key the client tried to set (`node_id`, `position`), and a param naming a "
        "resource this team cannot reach.\n\n"
        "A param the type does not declare is *not* an error: it is dropped, and the response "
        "reports the params the node actually holds. Nor is a param whose value the type cannot "
        "parse — that is stored and reported in `pipeline_errors`, the same as a missing required "
        "one, so a node can be built up over several calls."
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


class PipelineNodeEditView(GenericAPIView):
    """The façade's node endpoints: add a node, edit one, remove one.

    Serves both routes, so each ``path()`` narrows ``http_method_names`` to the verbs it offers --
    otherwise a PATCH to the collection route would reach ``patch`` with no ``node_id`` and raise
    instead of answering 405.

    Deleting a node is a *change* to the chatbot, not a deletion of it, so the stock
    ``DjangoModelPermissions`` verb map is replaced rather than extended.
    """

    permission_classes = [*BASE_PERMISSION_CLASSES, ChatbotCompositionPermission, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]
    # So that OPTIONS on the detail route describes its PATCH body, which stock DRF metadata leaves
    # out entirely.
    metadata_class = DescribesPatch
    # Only here so the generic view has a queryset; permissions are not derived from it.
    queryset = Pipeline.objects.none()

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
            "response reports and which you can then change with PATCH. The node is not wired to "
            "anything, so it appears in `unwired_handles` until you connect it — that is advisory, "
            "not an error.\n\n"
            "The node's `node_id` and its position on the canvas are assigned by the server and "
            "cannot be chosen. It is parked clear to the right of the nodes already there, and the "
            "End node is moved further right if the new node reaches it, so the End node stays the "
            "rightmost node on the canvas.\n\n"
            "What `params` may hold depends on `type`. "
            "`GET /api/v2/pipeline/nodes/{node_type}/` is the authoritative JSON Schema for one "
            "type, and `GET /api/v2/pipeline/options/{node_type}/` serves the ids its resource "
            "params may name."
        ),
        tags=["Pipelines"],
        parameters=[CHATBOT_ID],
        request=NodeCreateSerializer,
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
            edit_pipeline(request, id, lambda flow: plan_create(flow, node_type, label, params), self._write_response),
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
            "keyword that merely moved keeps its target on whichever handle it moved to. A renamed "
            "keyword counts as one branch gone and another new, so its edge goes and the new branch "
            "comes back unwired. Handle names are not stable across a keyword edit, so re-read "
            "`output_handles` after one.\n\n"
            "What `params` may hold depends on the node's type. "
            "`GET /api/v2/pipeline/nodes/{node_type}/` is the authoritative JSON Schema for one "
            "type, and `GET /api/v2/pipeline/options/{node_type}/` serves the ids its resource "
            "params may name."
        ),
        tags=["Pipelines"],
        parameters=[CHATBOT_ID, NODE_ID],
        request=NodeUpdateSerializer,
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
            edit_pipeline(
                request,
                id,
                # The node's type comes from the graph, so its params can only be checked under the
                # lock; the team goes in so the check can look its references up there.
                lambda flow: plan_update(flow, request.team, node_id, label, params),
                self._write_response,
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
        return Response(edit_pipeline(request, id, lambda flow: plan_delete(flow, node_id), self._write_response))

    @staticmethod
    def _write_response(pipeline: Pipeline, node_id: str | None) -> dict:
        """The response body every façade write returns: the pipeline's build state, and in front of
        it the node this write created or changed (absent when the write deleted one)."""
        state = pipeline_build_state(pipeline)
        body = {
            "pipeline_valid": state["pipeline_valid"],
            "pipeline_errors": state["errors"],
            "unwired_handles": state["unwired_handles"],
        }
        if node_id is None:
            return body
        node = next((node for node in pipeline.node_set.all() if node.flow_id == node_id), None)
        if node is None:
            # The patch engine applied the diff, so the row is there; not finding it means the
            # graph and the rows disagree, which is a bug here rather than anything the client did.
            raise APIException(f"Node '{node_id}' was written but could not be read back.")
        return {"node": WrittenNodeSerializer(node).data, **body}
