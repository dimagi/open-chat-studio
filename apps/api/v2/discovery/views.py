"""Team-level discovery endpoints for the chatbot write API.

These tell a client what it can build (`/pipeline/nodes/`) and which resource ids it may reference
(`/pipeline/options/`). Both read the shared helpers in ``apps.pipelines.node_metadata`` and reshape
them -- the builder consumes those helpers raw, so every client-facing transform lives in this
package. See ADR-0051 for why this view diverges from the builder's.
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
from apps.pipelines.nodes.base import OptionsSource
from apps.pipelines.nodes.node_metadata import get_node_default_values, get_node_parameter_values
from apps.service_providers.models import LlmProvider, LlmProviderModel, VoiceProvider
from apps.utils.prompt import PROMPT_VAR_DESCRIPTIONS

from .contract import HIDDEN_OPTION_KEYS, OPTIONS_KEY_RENAMES
from .node_types import etag, get_node_types, option_keys_for_node_type, unknown_node_type
from .serializers import NodeTypeNotFoundSerializer, NodeTypeSerializer, PipelineOptionsSerializer

# The option lists holding prompt variables rather than referenceable resource ids. Their entries are
# `{"label": v, "value": v}` pairs, which `_describe_prompt_vars` rewrites into `{label, description}`.
PROMPT_VAR_OPTION_SOURCES = (
    OptionsSource.jinja_node,
    OptionsSource.text_editor_autocomplete_vars_llm_node,
    OptionsSource.text_editor_autocomplete_vars_router_node,
)


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
                                },
                                "llm_provider_model_id": {
                                    "type": "integer",
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
        node_types = get_node_types()
        requested_type = request.query_params.get("type")
        if requested_type:
            node_types = [node for node in node_types if node["type"] == requested_type]
            if not node_types:
                raise unknown_node_type(requested_type)

        payload = self.get_serializer(node_types, many=True).data
        payload_etag = etag(payload)
        if request.headers.get("If-None-Match") == payload_etag:
            return HttpResponseNotModified()
        response = Response(payload)
        response["ETag"] = payload_etag
        return response


class PipelineOptionsView(DiscoveryView):
    @extend_schema(
        operation_id="pipeline_options",
        summary="List Pipeline Node Options",
        description=(
            "The values each node param accepts, scoped to the API key's team.\n\n"
            "A key holds the values for the node param of the same name: write one of "
            "`source_material`'s entries into a node's `source_material_id`, one of "
            "`collection_index`'s into `collection_index_ids`.\n\n"
            "Prompt variables are the exception, because two different params are both named `prompt` "
            "and they accept different sets. A template node's `template_string` draws from "
            "`prompt_variables`, an LLM node's `prompt` from `llm_prompt_variables`, and a router's "
            "`prompt` from `router_prompt_variables`. Pass `?node_type=` to receive only the list that "
            "applies -- the sets are not interchangeable."
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
                    "voice_provider_id": [{"value": 2, "label": "Prod Polly", "type": "aws"}],
                    "prompt_variables": [
                        {
                            "label": "input",
                            "description": "The text passed into this node from the preceding one.",
                        }
                    ],
                    "llm_prompt_variables": [
                        {
                            "label": "source_material",
                            "description": "The full text of the source material chosen in `source_material_id`.",
                        }
                    ],
                    "router_prompt_variables": [
                        {
                            "label": "session_state",
                            "description": "State that survives for the whole session.",
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
        wanted = option_keys_for_node_type(requested_type) if requested_type else None
        if requested_type and wanted is None:
            raise unknown_node_type(requested_type)

        team = request.team
        llm_providers = list(LlmProvider.objects.filter(team=team).values("id", "name", "type"))
        llm_provider_models = LlmProviderModel.objects.for_team(team)
        voice_providers = list(VoiceProvider.objects.filter(team=team))

        reachable_services = {provider.type.lower() for provider in voice_providers}
        synthetic_voices = (
            SyntheticVoice.get_for_team(team, [])
            .annotate(service_lower=Lower("service"))
            .filter(service_lower__in=reachable_services)
            if reachable_services
            else SyntheticVoice.objects.none()
        )

        options = self._clean_options(
            get_node_parameter_values(
                team=team,
                llm_providers=llm_providers,
                llm_provider_models=llm_provider_models,
                synthetic_voices=synthetic_voices,
            )
        )
        # No node param sources its options from this list -- it is here so a client can resolve the
        # `provider_id` carried on a `synthetic_voice_id` entry.
        options[OptionsSource.voice_provider_id] = [
            {"value": provider.id, "label": provider.name, "type": provider.type} for provider in voice_providers
        ]
        options["default_llm_provider"] = get_node_default_values(llm_providers, llm_provider_models)
        options = self._to_api_vocabulary(self._describe_prompt_vars(options))

        if wanted is not None:
            options = {key: value for key, value in options.items() if key in wanted}
        return Response(options)

    @classmethod
    def _clean_options(cls, value):
        """Strip builder-only affordances from an options payload.

        Two things the editor needs and a client must not see: placeholder entries with an empty
        ``value`` (a prompt like "Select a topic", not a referenceable id) and ``edit_url`` (a link
        into the Django UI). The walk recurses because ``built_in_tools`` is a dict of lists keyed by
        provider type, not a flat list.
        """
        if isinstance(value, dict):
            return {key: cls._clean_options(item) for key, item in value.items()}
        if isinstance(value, list):
            return [
                {key: item for key, item in option.items() if key != "edit_url"} if isinstance(option, dict) else option
                for option in value
                if not (isinstance(option, dict) and option.get("value") == "")
            ]
        return value

    @staticmethod
    def _describe_prompt_vars(options: dict) -> dict:
        """Swap each prompt variable's redundant ``value`` for a description of what it does.

        The builder emits these as ``{"label": v, "value": v}`` -- the two are always identical,
        since the value is just the name typed into the prompt. A human reading an autocomplete
        dropdown infers the rest from the name; a client cannot, so it gets the description instead.

        All three lists get the same treatment, so a client reads one entry shape whichever prompt it
        is filling. ``PROMPT_VAR_DESCRIPTIONS`` has to cover every variable any of them offers, which
        ``TestPromptVarDescriptions`` enforces -- a gap here is a 500, not a missing field.
        Mutates a copy of the list, never ``PROMPT_VAR_DESCRIPTIONS``.
        """
        for source in PROMPT_VAR_OPTION_SOURCES:
            if entries := options.get(source):
                options[source] = [
                    {"label": entry["label"], "description": PROMPT_VAR_DESCRIPTIONS[entry["label"]]}
                    for entry in entries
                ]
        return options

    @staticmethod
    def _to_api_vocabulary(options: dict) -> dict:
        """Drop the builder-only option lists and rename the keys the builder spells its own way."""
        return {
            OPTIONS_KEY_RENAMES.get(key, key): value for key, value in options.items() if key not in HIDDEN_OPTION_KEYS
        }
