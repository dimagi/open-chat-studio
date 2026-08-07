"""Team-level discovery endpoints for the chatbot write API.

These tell an agent what it can build (`/pipeline/nodes/`) and which resource ids it may reference
(`/pipeline/options/`). Both read the shared helpers in ``apps.pipelines.node_options`` and reshape
them here -- the builder consumes those helpers raw, so every agent-facing transform must stay in
this module.
"""

from functools import cache

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.api.permissions import DjangoModelPermissionsWithView
from apps.experiments.models import SyntheticVoice
from apps.oauth.permissions import TokenHasOAuthResourceScope
from apps.pipelines.models import Pipeline
from apps.pipelines.node_options import get_node_default_values, get_node_parameter_values, get_node_schemas
from apps.service_providers.models import LlmProvider, LlmProviderModel, VoiceProvider


class NodeTypeSerializer(serializers.Serializer):
    type = serializers.CharField(help_text="Write this to a node's `type` field.")
    description = serializers.CharField()
    can_add = serializers.BooleanField(
        help_text=(
            "False for structural nodes the server manages itself -- Start, End and Passthrough. "
            "They are listed so the agent can resolve types it reads from /inspect/, not so it can "
            "create them."
        )
    )
    schema = serializers.DictField(help_text="JSON Schema for the node's `params`.")


@cache
def _node_types() -> list[dict]:
    """Node types reshaped for agent consumption.

    Cached because the node classes are fixed at import time, so this is static per deploy.
    """
    node_types = []
    for schema in get_node_schemas():
        if schema.get("ui:deprecated"):
            continue
        node_types.append(
            {
                "type": schema["title"],
                "description": schema["description"],
                "can_add": bool(schema.get("ui:can_add")),
                "schema": {key: value for key, value in schema.items() if not key.startswith("ui:")},
            }
        )
    return node_types


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
            "The node types a pipeline can contain, with the JSON schema of each one's `params`. "
            "Deprecated types are omitted. Types with `can_add: false` are managed by the server "
            "and must not be created."
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
        responses={200: NodeTypeSerializer(many=True)},
    )
    def get(self, request):
        node_types = _node_types()
        requested_type = request.query_params.get("type")
        if requested_type:
            node_types = [node for node in node_types if node["type"] == requested_type]
            if not node_types:
                raise NotFound(f"Unknown node type: {requested_type}")
        return Response(self.get_serializer(node_types, many=True).data)


def _clean_options(value):
    """Strip builder-only affordances from an options payload.

    Two things the editor needs and an agent must not see: placeholder entries with an empty
    ``value`` (a prompt like "Select a topic", not a referenceable id) and ``edit_url`` (a link into
    the Django UI). The walk recurses because ``built_in_tools`` is a dict of lists keyed by provider
    type, not a flat list.
    """
    if isinstance(value, dict):
        return {key: _clean_options(item) for key, item in value.items()}
    if isinstance(value, list):
        return [
            {key: item for key, item in option.items() if key != "edit_url"} if isinstance(option, dict) else option
            for option in value
            if not (isinstance(option, dict) and option.get("value") == "")
        ]
    return value


class PipelineOptionsView(DiscoveryView):
    @extend_schema(
        operation_id="pipeline_options",
        summary="List Pipeline Node Options",
        description=(
            "The values each node param accepts, scoped to the API key's team: resource ids to "
            "reference, tool names, and the provider defaults a new node is created with. Keys "
            "match the node schemas' option sources."
        ),
        tags=["Pipelines"],
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        team = request.team
        llm_providers = list(LlmProvider.objects.filter(team=team).values("id", "name", "type"))
        llm_provider_models = LlmProviderModel.objects.for_team(team)

        options = _clean_options(
            get_node_parameter_values(
                team=team,
                llm_providers=llm_providers,
                llm_provider_models=llm_provider_models,
                synthetic_voices=SyntheticVoice.get_for_team(team, []),
            )
        )
        options["VoiceProviderId"] = [
            {"value": provider.id, "label": provider.name, "type": provider.type}
            for provider in VoiceProvider.objects.filter(team=team)
        ]
        options["default_values"] = get_node_default_values(llm_providers, llm_provider_models)
        return Response(options)
