"""Team-level discovery endpoints for the chatbot write API.

These tell an agent what it can build (`/pipeline/nodes/`) and which resource ids it may reference
(`/pipeline/options/`). Both read the shared helpers in ``apps.pipelines.node_options`` and reshape
them -- the builder consumes those helpers raw, so every agent-facing transform lives in this
package. See ADR-0051 for why the agent's view diverges from the builder's.
"""

from django.db.models.functions import Lower
from django.http import HttpResponseNotModified
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.api.permissions import DjangoModelPermissionsWithView
from apps.experiments.models import SyntheticVoice
from apps.oauth.permissions import TokenHasOAuthResourceScope
from apps.pipelines.models import Pipeline
from apps.pipelines.node_options import get_node_default_values, get_node_parameter_values
from apps.service_providers.models import LlmProvider, LlmProviderModel, VoiceProvider

from .node_types import _etag, _node_types, _unknown_node_type
from .options import _clean_options, _describe_prompt_vars, _keys_for_node_type, _rename_option_keys
from .serializers import NodeTypeNotFoundSerializer, NodeTypeSerializer, PipelineOptionsSerializer


class DiscoveryView(GenericAPIView):
    """Shared auth for the discovery endpoints.

    ``queryset`` exists only so DjangoModelPermissions can derive ``pipelines.view_pipeline`` from a
    model; nothing is ever fetched through it.
    """

    permission_classes = [DjangoModelPermissionsWithView, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]  # TokenHasResourceScope maps GET -> chatbots:read
    queryset = Pipeline.objects.none()
    # GenericAPIView defaults pagination_class to the project-wide cursor paginator, and drf-
    # spectacular trusts it even though these views never call paginate_queryset(). Without this,
    # the generated schema would falsely document a paginated envelope (`results`/`next`/`cursor`)
    # for an endpoint that always returns a bare JSON array.
    pagination_class = None


class PipelineNodesView(DiscoveryView):
    serializer_class = NodeTypeSerializer

    @extend_schema(
        operation_id="pipeline_node_list",
        summary="List Pipeline Node Types",
        description=(
            "The node types a pipeline can contain, with the JSON Schema of each one's `params` and "
            "the outputs its edges leave by. Deprecated types are omitted.\n\n"
            "Static per deploy: revalidate a cached copy with `If-None-Match` against the `ETag`."
        ),
        tags=["Pipelines"],
        parameters=[
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Return only this node type. An unknown or deprecated name returns 404.",
            )
        ],
        responses={
            200: NodeTypeSerializer(many=True),
            404: OpenApiResponse(
                response=NodeTypeNotFoundSerializer,
                description="No such node type, or the type is deprecated and therefore not listed.",
            ),
        },
        examples=[
            OpenApiExample(
                name="NodeTypes",
                summary="A router, showing the per-keyword outputs and a param-pairing rule.",
                value=[
                    {
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
                                    "options_source": "llm_provider_id",
                                },
                                "llm_provider_model_id": {
                                    "type": "integer",
                                    "options_source": "llm_provider_model_id",
                                    "must_match": {"field": "llm_provider_id", "on": "type"},
                                },
                            },
                        },
                    }
                ],
                response_only=True,
            )
        ],
    )
    def get(self, request):
        node_types = _node_types()
        requested_type = request.query_params.get("type")
        if requested_type:
            node_types = [node for node in node_types if node["type"] == requested_type]
            if not node_types:
                raise _unknown_node_type(requested_type)

        payload = self.get_serializer(node_types, many=True).data
        etag = _etag(payload)
        if request.headers.get("If-None-Match") == etag:
            return HttpResponseNotModified()
        response = Response(payload)
        response["ETag"] = etag
        return response


class PipelineOptionsView(DiscoveryView):
    @extend_schema(
        operation_id="pipeline_options",
        summary="List Pipeline Node Options",
        description=(
            "The values each node param accepts, scoped to the API key's team.\n\n"
            "Each key is named by some node param's `options_source` in `/pipeline/nodes/`; resolve a "
            "param through that rather than matching key names by eye."
        ),
        tags=["Pipelines"],
        parameters=[
            OpenApiParameter(
                name="node_type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Return only the keys this node type's params can reference, plus its "
                    "`default_llm_provider` where it has one. An unknown or deprecated name returns 404."
                ),
            )
        ],
        responses={
            200: PipelineOptionsSerializer,
            404: OpenApiResponse(
                response=NodeTypeNotFoundSerializer,
                description="No such node type, or the type is deprecated and therefore not listed.",
            ),
        },
        examples=[
            OpenApiExample(
                name="PipelineOptions",
                summary="A flat option list, a nested built-in-tools block, and the provider defaults.",
                value={
                    "llm_provider_id": [{"value": 1, "label": "Prod OpenAI", "type": "openai"}],
                    "source_material": [{"value": 3, "label": "Returns policy"}],
                    "collection": [{"value": 7, "label": "Policy docs"}],
                    "collection_index": [{"value": 9, "label": "Support KB (Remote)"}],
                    "built_in_tools": {"openai": [{"value": "web-search", "label": "Web Search"}]},
                    "built_in_tools_config": {
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
                    "voice_provider_id": [{"value": 2, "label": "Prod Polly", "type": "aws"}],
                    "jinja_node": [
                        {
                            "label": "input",
                            "description": "The text passed into this node from the preceding one.",
                        }
                    ],
                    "default_llm_provider": {"llm_provider_id": 1, "llm_provider_model_id": 5},
                },
                response_only=True,
            )
        ],
    )
    def get(self, request):
        requested_type = request.query_params.get("node_type")
        wanted = _keys_for_node_type(requested_type) if requested_type else None
        if requested_type and wanted is None:
            raise _unknown_node_type(requested_type)

        team = request.team
        llm_providers = list(LlmProvider.objects.filter(team=team).values("id", "name", "type"))
        llm_provider_models = LlmProviderModel.objects.for_team(team)
        voice_providers = list(VoiceProvider.objects.filter(team=team))

        # SyntheticVoice.service ("AWS", "Azure", ...) and VoiceProviderType ("aws", "azure", ...) differ
        # only in case, so a plain `service__in` against the team's provider types matches nothing -- see
        # the chatbot builder's `service__iexact` for the same pairing. Without a team-owned provider for
        # a service, its voices are unreachable and must not be listed.
        reachable_services = {provider.type.lower() for provider in voice_providers}
        synthetic_voices = (
            SyntheticVoice.get_for_team(team, [])
            .annotate(service_lower=Lower("service"))
            .filter(service_lower__in=reachable_services)
            if reachable_services
            else SyntheticVoice.objects.none()
        )

        options = _clean_options(
            get_node_parameter_values(
                team=team,
                llm_providers=llm_providers,
                llm_provider_models=llm_provider_models,
                synthetic_voices=synthetic_voices,
            )
        )
        options["VoiceProviderId"] = [
            {"value": provider.id, "label": provider.name, "type": provider.type} for provider in voice_providers
        ]
        options["default_llm_provider"] = get_node_default_values(llm_providers, llm_provider_models)
        options = _rename_option_keys(_describe_prompt_vars(options))

        if wanted is not None:
            options = {key: value for key, value in options.items() if key in wanted}
        return Response(options)
