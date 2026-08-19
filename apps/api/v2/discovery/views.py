"""Team-level discovery endpoints for the chatbot write API: what a client can build
(`/pipeline/nodes/`) and which resource ids it may reference (`/pipeline/options/`).

Both reshape the shared helpers in ``apps.pipelines.nodes.node_metadata``, which the builder consumes
raw. The reshaping rules live in ``contract.py`` and ``node_types.py``.
"""

from django.db.models import QuerySet
from django.db.models.functions import Lower
from django.http import HttpResponseNotModified
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.api.permissions import BASE_PERMISSION_CLASSES, DjangoModelPermissionsWithView
from apps.experiments.models import SyntheticVoice
from apps.oauth.permissions import TokenHasOAuthResourceScope
from apps.pipelines.models import Pipeline
from apps.pipelines.nodes.base import OptionsSource
from apps.pipelines.nodes.node_metadata import get_node_default_values, get_node_parameter_values
from apps.service_providers.models import LlmProvider, LlmProviderModel, VoiceProvider
from apps.utils.prompt import PROMPT_VAR_DESCRIPTIONS

from .node_types import etag, get_node_types, option_keys_for_node_type, served_option_keys, unknown_node_type
from .serializers import NodeTypeNotFoundSerializer, NodeTypeSerializer, PipelineOptionsSerializer

# The option lists holding prompt variables rather than referenceable resource ids.
PROMPT_VAR_OPTION_SOURCES = (
    OptionsSource.template_variables,
    OptionsSource.llm_prompt_variables,
    OptionsSource.router_prompt_variables,
)


class DiscoveryView(GenericAPIView):
    """Shared auth for the discovery endpoints."""

    permission_classes = [*BASE_PERMISSION_CLASSES, DjangoModelPermissionsWithView, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]  # TokenHasResourceScope maps GET -> chatbots:read
    # Only here so DjangoModelPermissions can derive `pipelines.view_pipeline` from a model.
    queryset = Pipeline.objects.none()
    # Without this drf-spectacular documents a paginated envelope around the bare JSON array.
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
                # One entry, not a list: drf-spectacular wraps a `many=True` response example itself.
                value={
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
                },
                response_only=True,
            )
        ],
    )
    def get(self, request):
        node_types = self._requested_node_types(request.query_params.get("type"))
        return self._etagged(request, self.get_serializer(node_types, many=True).data)

    @staticmethod
    def _requested_node_types(requested_type: str | None) -> list[dict]:
        """Every listed node type, or just the one `?type=` named."""
        node_types = get_node_types()
        if not requested_type:
            return node_types
        matching = [node for node in node_types if node["type"] == requested_type]
        if not matching:
            raise unknown_node_type(requested_type)
        return matching

    @staticmethod
    def _etagged(request, payload) -> Response | HttpResponseNotModified:
        """`payload` under its `ETag`, or a bare 304 for a client whose cached copy still matches."""
        payload_etag = etag(payload)
        if request.headers.get("If-None-Match") == payload_etag:
            return HttpResponseNotModified()
        response = Response(payload)
        response["ETag"] = payload_etag
        return response


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


class PipelineOptionsView(DiscoveryView):
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
            "from `router_prompt_variables`, both written single-braced (`{source_material}`). Pass "
            "`?node_type=` to receive only the list that applies -- the sets are not interchangeable."
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
                summary="Every key the endpoint serves, for a team that has one of each resource.",
                value=PIPELINE_OPTIONS_EXAMPLE,
                response_only=True,
            )
        ],
    )
    def get(self, request):
        wanted = self._wanted_keys(request.query_params.get("node_type"))
        options = self._options_for_team(request.team)
        return Response({key: value for key, value in options.items() if key in wanted})

    @staticmethod
    def _wanted_keys(requested_type: str | None) -> frozenset[str]:
        """The keys this response carries: everything served, or just what one node type can read."""
        if not requested_type:
            return served_option_keys()
        wanted = option_keys_for_node_type(requested_type)
        if wanted is None:
            raise unknown_node_type(requested_type)
        return wanted

    @classmethod
    def _options_for_team(cls, team) -> dict:
        """Every option list the team can draw on, with the builder-only affordances stripped.
        Scoping to a node type happens after this."""
        llm_providers = list(LlmProvider.objects.filter(team=team).values("id", "name", "type"))
        llm_provider_types = {provider["type"] for provider in llm_providers}
        voice_providers = list(VoiceProvider.objects.filter(team=team))

        # A model the team holds no provider for cannot be called, so it is not an option. Models with
        # no team are shared with every team, which makes this the only filter that scopes them.
        # Deprecated models are absent rather than flagged, the same as a deprecated node type.
        llm_provider_models = LlmProviderModel.objects.for_team(team).filter(
            type__in=llm_provider_types, deprecated=False
        )

        options = cls._clean_options(
            get_node_parameter_values(
                team=team,
                llm_providers=llm_providers,
                llm_provider_models=llm_provider_models,
                synthetic_voices=cls._speakable_voices(team, voice_providers),
            )
        )
        options[OptionsSource.tool_config] = {
            provider_type: config
            for provider_type, config in options[OptionsSource.tool_config].items()
            if provider_type in llm_provider_types
        }
        options["default_llm_provider"] = get_node_default_values(llm_providers, llm_provider_models)
        return cls._describe_prompt_vars(options)

    @staticmethod
    def _speakable_voices(team, voice_providers: list) -> QuerySet:
        """The voices the team has a provider to speak. `SyntheticVoice.service` ("AWS") and the
        provider type ("aws") differ in case, so the match is made on a lowered annotation."""
        reachable_services = {provider.type.lower() for provider in voice_providers}
        if not reachable_services:
            return SyntheticVoice.objects.none()
        return (
            SyntheticVoice.get_for_team(team, [])
            .annotate(service_lower=Lower("service"))
            .filter(service_lower__in=reachable_services)
        )

    @classmethod
    def _clean_options(cls, value):
        """Strip the builder-only affordances off every option list. Recurses -- ``built_in_tools``
        and ``tool_config`` nest their lists inside dicts keyed by provider type."""
        if isinstance(value, dict):
            return {key: cls._clean_options(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._clean_option(option) for option in value if not cls._is_placeholder(option)]
        return value

    @staticmethod
    def _is_placeholder(option) -> bool:
        """A builder entry standing in for "nothing chosen". It names no resource to reference."""
        return isinstance(option, dict) and option.get("value") == ""

    @staticmethod
    def _clean_option(option):
        """One option entry, with its ``edit_url`` link into the Django UI dropped."""
        if not isinstance(option, dict):
            return option
        return {key: item for key, item in option.items() if key != "edit_url"}

    @staticmethod
    def _describe_prompt_vars(options: dict) -> dict:
        """Swap each prompt variable's redundant ``value`` (always equal to its ``label``) for a
        description of what the variable holds. An uncovered variable is a KeyError here, which
        ``test_every_offered_prompt_var_has_a_description`` guards against."""
        for source in PROMPT_VAR_OPTION_SOURCES:
            if entries := options.get(source):
                options[source] = [
                    {"label": entry["label"], "description": PROMPT_VAR_DESCRIPTIONS[entry["label"]]}
                    for entry in entries
                ]
        return options
