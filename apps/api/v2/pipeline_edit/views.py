"""The pipeline façade's node endpoints (#4140).

A chatbot's pipeline is edited one node at a time rather than replaced wholesale: the server holds
the graph and applies the change, so a client never has to reproduce a whole document to alter one
setting, and two edits to different nodes cannot overwrite each other.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.api.permissions import BASE_PERMISSION_CLASSES
from apps.api.v2.discovery.node_types import get_node_class
from apps.api.v2.write.base import ChatbotCompositionPermission
from apps.oauth.permissions import TokenHasOAuthResourceScope
from apps.pipelines.build_state import pipeline_build_state
from apps.pipelines.models import Pipeline

from .facade import edit_pipeline
from .graph_editor import plan_create
from .node_params import writable_params
from .serializers import NodeCreateSerializer, NodeWriteSerializer, WrittenNodeSerializer

CHATBOT_ID = OpenApiParameter(
    name="id", type=OpenApiTypes.UUID, location=OpenApiParameter.PATH, description="Chatbot ID"
)
BAD_REQUEST = OpenApiResponse(
    description=(
        "The body is not one this endpoint can act on. Errors are keyed by the field at fault, and "
        "param-level errors are nested under `params`. This covers an unrecognised body key and a "
        "server-assigned key the client tried to set (`node_id`, `position`).\n\n"
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


class PipelineNodeEditView(GenericAPIView):
    """The façade's node endpoints: add a node to a chatbot's working pipeline.

    Editing a chatbot's composition is a *change* to the chatbot whatever the verb, so the stock
    ``DjangoModelPermissions`` verb map is replaced rather than extended.
    """

    permission_classes = [*BASE_PERMISSION_CLASSES, ChatbotCompositionPermission, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]
    serializer_class = NodeCreateSerializer
    # Only here so the generic view has a queryset; permissions are not derived from it.
    queryset = Pipeline.objects.none()

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
        return Response(
            edit_pipeline(request, id, lambda flow: plan_create(flow, node_type, label, params), self._write_response),
            status=status.HTTP_201_CREATED,
        )

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
