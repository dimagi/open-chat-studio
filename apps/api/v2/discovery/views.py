"""Team-level discovery endpoints for the chatbot write API: what a client can build
(`/pipeline/nodes/`) and which resource ids it may reference (`/pipeline/options/`).

Both reshape the shared helpers in ``apps.pipelines.nodes.node_metadata``, which the builder consumes
raw. The reshaping rules live in ``contract.py`` and ``node_types.py``.
"""

from django.http import HttpResponseNotModified
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.api.permissions import BASE_PERMISSION_CLASSES, DjangoModelPermissionsWithView
from apps.oauth.permissions import TokenHasOAuthResourceScope
from apps.pipelines.models import Pipeline

from .node_types import (
    etag,
    get_node_type,
    get_node_types,
    option_keys_for_node_type,
    served_option_keys,
    unknown_node_type,
)
from .options import options_for_team
from .serializers import NodeTypeNotFoundSerializer, NodeTypeSerializer, PipelineOptionsSerializer


class DiscoveryView(GenericAPIView):
    """Shared auth for the discovery endpoints."""

    permission_classes = [*BASE_PERMISSION_CLASSES, DjangoModelPermissionsWithView, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]  # TokenHasResourceScope maps GET -> chatbots:read
    # Only here so DjangoModelPermissions can derive `pipelines.view_pipeline` from a model.
    queryset = Pipeline.objects.none()
    # Without this drf-spectacular documents a paginated envelope around the bare JSON array.
    pagination_class = None


# The documented response sample for a single node type. The list endpoint reuses it -- one entry,
# not a list, because drf-spectacular wraps a `many=True` response example itself.
NODE_TYPE_EXAMPLE = {
    "type": "RouterNode",
    "description": "Routes the input to one of the linked nodes using an LLM",
    "documentation_url": "https://docs.openchatstudio.com/how-to/pipelines/nodes/",
    "outputs": {
        "kind": "per_keyword",
        "handles": None,
        "handle_pattern": "output_{index}",
        "description": "One output per entry in `keywords`.",
    },
    "schema": {
        "title": "RouterNode",
        "type": "object",
        "required": ["llm_provider_id", "llm_provider_model_id", "name"],
        "properties": {
            "llm_provider_id": {
                "type": "integer",
                "title": "LLM Model",
                "description": "The configured LLM service provider this node calls.",
            },
            "llm_provider_model_id": {
                "type": "integer",
                "must_match": {"field": "llm_provider_id", "on": "type"},
            },
        },
    },
}


class NodeTypesView(DiscoveryView):
    """Shared serializer and revalidation for the two node-type endpoints. Both payloads are static
    per deploy, so both are served under an `ETag`."""

    serializer_class = NodeTypeSerializer

    @staticmethod
    def _etagged(request, payload: list | dict) -> Response | HttpResponseNotModified:
        """`payload` under its `ETag`, or a bare 304 for a client whose cached copy still matches."""
        payload_etag = etag(payload)
        if request.headers.get("If-None-Match") == payload_etag:
            return HttpResponseNotModified()
        response = Response(payload)
        response["ETag"] = payload_etag
        return response


class PipelineNodesView(NodeTypesView):
    @extend_schema(
        operation_id="pipeline_node_list",
        summary="List Pipeline Node Types",
        description=(
            "The node types a pipeline can contain, with the JSON Schema of each one's `params` and "
            "the outputs its edges leave by. Deprecated types are omitted.\n\n"
            "Static per deploy: revalidate a cached copy with `If-None-Match` against the `ETag`."
        ),
        tags=["Pipelines"],
        responses={200: NodeTypeSerializer(many=True)},
        examples=[
            OpenApiExample(
                name="NodeTypes",
                summary="A router, showing the per-keyword outputs and a param-pairing rule.",
                value=NODE_TYPE_EXAMPLE,
                response_only=True,
            )
        ],
    )
    def get(self, request):
        return self._etagged(request, self.get_serializer(get_node_types(), many=True).data)


class PipelineNodeView(NodeTypesView):
    @extend_schema(
        operation_id="pipeline_node_retrieve",
        summary="Retrieve a Pipeline Node Type",
        description=(
            "One node type, as `/pipeline/nodes/` serves it. A deprecated type is not retrievable "
            "either -- it is not something a client may build.\n\n"
            "Static per deploy: revalidate a cached copy with `If-None-Match` against the `ETag`."
        ),
        tags=["Pipelines"],
        parameters=[
            OpenApiParameter(
                name="node_type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="The type's name, as carried in the `type` field of a `/pipeline/nodes/` entry.",
            )
        ],
        responses={
            200: NodeTypeSerializer,
            404: OpenApiResponse(
                response=NodeTypeNotFoundSerializer,
                description="No such node type, or the type is deprecated and therefore not listed.",
            ),
        },
        examples=[
            OpenApiExample(
                name="NodeType",
                summary="A router, showing the per-keyword outputs and a param-pairing rule.",
                value=NODE_TYPE_EXAMPLE,
                response_only=True,
            )
        ],
    )
    def get(self, request, node_type):
        return self._etagged(request, self.get_serializer(get_node_type(node_type)).data)


# The documented response sample. Kept whole rather than inline so a test can hold it to the
# serializer -- a key the endpoint serves but the sample omits reads as a key that doesn't exist.
PIPELINE_OPTIONS_EXAMPLE = {
    "llm_provider_id": [{"value": 1, "label": "Prod OpenAI", "type": "openai"}],
    "llm_provider_model_id": [{"value": 5, "label": "gpt-4o", "type": "openai", "max_token_limit": 128000}],
    "synthetic_voice_id": [{"value": 11, "label": "Joanna (English)", "type": "aws", "provider_id": 2}],
    "source_material": [{"value": 3, "label": "Returns policy"}],
    "collection": [{"value": 7, "label": "Policy docs"}],
    "collection_index": [{"value": 9, "label": "Support KB (Remote)"}],
    "tools": [{"value": "one-off-reminder", "label": "One-off Reminder"}],
    "custom_actions": [{"value": "4:getOrderStatus", "label": "Orders API: Look up an order"}],
    "built_in_tools": {"openai": [{"value": "web-search", "label": "Web Search"}]},
    "tool_config": {
        "anthropic": {
            "web-search": [
                {
                    "name": "allowed_domains",
                    "type": "expandable_text",
                    "label": "Allowed Domains",
                    "helpText": "Only search these domains. Separate entries with newlines.",
                }
            ]
        }
    },
    "template_variables": [{"label": "input", "description": "The text passed into this node from the preceding one."}],
    "llm_prompt_variables": [
        {
            "label": "source_material",
            "description": "The full text of the source material chosen in `source_material_id`.",
        }
    ],
    "router_prompt_variables": [
        {"label": "session_state", "description": "State that survives for the whole session."}
    ],
    "default_llm_provider": {"llm_provider_id": 1, "llm_provider_model_id": 5},
}


class TeamOptionsView(DiscoveryView):
    """Shared payload for the two option endpoints. Both build every option list the team can draw
    on (``options_for_team``) and differ only in which keys they keep."""

    serializer_class = PipelineOptionsSerializer


class PipelineOptionsView(TeamOptionsView):
    @extend_schema(
        operation_id="pipeline_options",
        summary="List Pipeline Node Options",
        description=(
            "The values each node param accepts, scoped to the API key's team.\n\n"
            "A key holds the values for the node param of the same name: write one of "
            "`source_material`'s entries into a node's `source_material_id`, one of "
            "`collection_index`'s into `collection_index_ids`.\n\n"
            "The variable lists are the exception, because no param is named for the list it draws "
            "on and two different params are both named `prompt`. Jinja params -- `template_string` "
            "and `SendEmail`'s fields -- draw from `template_variables` and are written double-braced "
            "(`{{input}}`); an LLM node's `prompt` draws from `llm_prompt_variables` and a router's "
            "from `router_prompt_variables`, both written single-braced (`{source_material}`). Fetch "
            "`/pipeline/options/{node_type}/` to receive only the list that applies -- the sets are "
            "not interchangeable."
        ),
        tags=["Pipelines"],
        responses={200: PipelineOptionsSerializer},
        examples=[
            OpenApiExample(
                name="PipelineOptions",
                summary="Every key the endpoint serves, for a team that has one of each resource.",
                value=PIPELINE_OPTIONS_EXAMPLE,
                response_only=True,
            )
        ],
    )
    def get(self, request):
        served = served_option_keys()
        options = options_for_team(request.team)
        filtered = {key: value for key, value in options.items() if key in served}
        return Response(self.get_serializer(filtered).data)


class PipelineNodeOptionsView(TeamOptionsView):
    @extend_schema(
        operation_id="pipeline_node_options",
        summary="List One Node Type's Options",
        description=(
            "The keys this node type's params can reference, plus its `default_llm_provider` where "
            "it has one -- `/pipeline/options/` cut down to what building this one node needs. Which "
            "of the three variable lists applies is settled here rather than left to the client: the "
            "sets are not interchangeable."
        ),
        tags=["Pipelines"],
        parameters=[
            OpenApiParameter(
                name="node_type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="The type's name, as carried in the `type` field of a `/pipeline/nodes/` entry.",
            )
        ],
        responses={
            200: PipelineOptionsSerializer,
            404: OpenApiResponse(
                response=NodeTypeNotFoundSerializer,
                description="No such node type, or the type is deprecated and therefore not listed.",
            ),
        },
    )
    def get(self, request, node_type):
        wanted = option_keys_for_node_type(node_type)
        if wanted is None:
            raise unknown_node_type(node_type)
        options = options_for_team(request.team)
        filtered = {key: value for key, value in options.items() if key in wanted}
        return Response(self.get_serializer(filtered).data)
