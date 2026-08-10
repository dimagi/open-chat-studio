"""Team-level discovery endpoints for the chatbot write API.

These tell an agent what it can build (`/pipeline/nodes/`) and which resource ids it may reference
(`/pipeline/options/`). Both read the shared helpers in ``apps.pipelines.node_options`` and reshape
them here -- the builder consumes those helpers raw, so every agent-facing transform must stay in
this module.

The contract between the two endpoints is one rule with no exceptions: a param's ``options_source``
names the key in ``/pipeline/options/`` holding its permitted values. Where the builder never
declared that link (it hard-codes a widget instead) it is synthesised here rather than left to the
agent to infer. See ADR-0051 for why the agent's view diverges from the builder's.
"""

import hashlib
import json
from functools import cache

from django.conf import settings
from django.db.models.functions import Lower
from django.http import HttpResponseNotModified
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.api.permissions import DjangoModelPermissionsWithView
from apps.experiments.models import SyntheticVoice
from apps.oauth.permissions import TokenHasOAuthResourceScope
from apps.pipelines.models import Pipeline
from apps.pipelines.node_options import get_node_default_values, get_node_parameter_values, get_node_schemas
from apps.pipelines.nodes.base import OptionsSource, PipelineRouterNode, resolve_node_class
from apps.service_providers.models import LlmProvider, LlmProviderModel, VoiceProvider
from apps.utils.prompt import PROMPT_VAR_DESCRIPTIONS

# --------------------------------------------------------------------------------------------
# The param -> options-key join
# --------------------------------------------------------------------------------------------

# `OptionsSource` names that are not snake_case. The builder's JS reads these keys verbatim
# (assets/javascript/apps/pipeline/nodes/widgets.tsx), so they are renamed on the API surface only.
OPTIONS_KEY_RENAMES = {
    "LlmProviderId": "llm_provider_id",
    "LlmProviderModelId": "llm_provider_model_id",
    "VoiceProviderId": "voice_provider_id",
}

# Params whose values come from an options key the builder never declared, because it renders them
# with a bespoke widget rather than the generic `select`. Without these the join has four exceptions
# and the agent has to guess them from naming.
IMPLIED_OPTIONS_SOURCE = {
    "llm_provider_id": "llm_provider_id",
    "llm_provider_model_id": "llm_provider_model_id",
    "tool_config": "built_in_tools_config",
    "synthetic_voice_id": "synthetic_voice_id",
}

# Both the model and the provider carry a `type` ("openai", "anthropic", ...) and the two must agree;
# `get_node_default_values` silently relies on this when it picks the pair a new node starts with.
PROVIDER_TYPE_MATCH = {"field": "llm_provider_id", "on": "type"}

# `param -> the field whose chosen option this param's value must line up with`.
MUST_MATCH = {"llm_provider_model_id": PROVIDER_TYPE_MATCH}

# `param -> the field whose chosen option selects which sub-list of its options apply`. Both of
# these option keys are dicts keyed by provider type rather than flat lists.
OPTIONS_KEYED_BY = {"built_in_tools": PROVIDER_TYPE_MATCH, "tool_config": PROVIDER_TYPE_MATCH}

# `ui:*` property keys that carry meaning for an agent, renamed out of the builder's vocabulary.
# Everything else `ui:*` is presentation (`ui:widget`, `ui:rows`, `ui:onShowDefault`) or duplicates
# the JSON Schema (`ui:enumLabels` restates `enum`) and is dropped.
UI_KEY_TRANSLATIONS = {
    "ui:optionsSource": "options_source",
    "ui:visibleWhen": "applies_when",
    "ui:flagRequired": "requires_feature_flag",
}

SINGLE_OUTPUT = {
    "kind": "single",
    "handles": ["output"],
    "description": "One output, handle `output`. Every edge leaving this node uses it.",
}
PER_KEYWORD_OUTPUT = {
    "kind": "per_keyword",
    "handles": None,
    "handle_pattern": "output_{index}",
    "description": (
        "One output per entry in `keywords`: entry `i` is served by handle `output_i`, so an edge "
        "leaving this node must set `source_handle` to match the route it represents. The run ends "
        "if the chosen handle has no edge."
    ),
}


# --------------------------------------------------------------------------------------------
# Serializers -- these document the OpenAPI schema; the views build plain dicts
# --------------------------------------------------------------------------------------------


class NodeOutputsSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(
        choices=["single", "per_keyword", "none"],
        help_text="`single` for one fixed output, `per_keyword` for one per `keywords` entry, `none` for a terminus.",
    )
    handles = serializers.ListField(
        child=serializers.CharField(),
        allow_null=True,
        help_text="The `source_handle` values an edge may use, or null when they depend on the node's params.",
    )
    handle_pattern = serializers.CharField(
        required=False, help_text="How to build a handle name when `handles` is null."
    )
    description = serializers.CharField()


class NodeTypeSerializer(serializers.Serializer):
    type = serializers.CharField(help_text="Write this to a node's `type` field.")
    description = serializers.CharField()
    documentation_url = serializers.URLField(
        required=False, help_text="Human documentation for this node type, where it exists."
    )
    outputs = NodeOutputsSerializer(help_text="How many outputs the node has and how edges address them.")
    schema = serializers.DictField(
        help_text=(
            "JSON Schema for the node's `params`. Properties carry these keys beyond standard JSON "
            "Schema, where they apply: `options_source` (the `/pipeline/options/` key holding this "
            "param's permitted values), `must_match` (this value must agree with another param's "
            "chosen option on the named attribute), `options_keyed_by` (the option list is a dict, "
            "and another param's chosen option selects the sub-list), `applies_when` (the param is "
            "ignored unless the condition holds) and `requires_feature_flag`."
        )
    )


class NodeTypeNotFoundSerializer(serializers.Serializer):
    """The body DRF renders for the `NotFound` raised on an unknown `type`."""

    detail = serializers.CharField()
    valid_types = serializers.ListField(
        child=serializers.CharField(), help_text="Every type this endpoint serves, so a failed call can be corrected."
    )


class OptionSerializer(serializers.Serializer):
    """One selectable value. Write `value` into the param; `label` is for humans reading a diff."""

    value = serializers.CharField(
        help_text=(
            "Write this into the node param. Opaque -- copy it verbatim and never construct one. "
            "`mcp_tools` and `custom_actions` values in particular are composite identifiers."
        )
    )
    label = serializers.CharField()
    type = serializers.CharField(
        required=False, help_text="The provider type, where the option belongs to one. The join key for `must_match`."
    )
    provider_id = serializers.IntegerField(required=False, help_text="The provider this option belongs to.")
    max_token_limit = serializers.IntegerField(required=False)


class PromptVariableSerializer(serializers.Serializer):
    """A template variable rather than a resource id -- there is no `value` to write."""

    label = serializers.CharField(help_text="Write this into the prompt or template, in braces.")
    description = serializers.CharField(help_text="What the variable holds and when to use it.")


class DefaultLlmProviderSerializer(serializers.Serializer):
    llm_provider_id = serializers.IntegerField(allow_null=True)
    llm_provider_model_id = serializers.IntegerField(allow_null=True)


class PipelineOptionsSerializer(serializers.Serializer):
    """The documented keys. A response carries a subset when `?node_type=` is given, and may carry
    keys not listed here as new node params are added -- resolve them through `options_source`
    rather than against this list."""

    llm_provider_id = OptionSerializer(many=True, required=False)
    llm_provider_model_id = OptionSerializer(many=True, required=False)
    voice_provider_id = OptionSerializer(
        many=True,
        required=False,
        help_text="The team's configured voice providers. No node param sources its options from this.",
    )
    synthetic_voice_id = OptionSerializer(many=True, required=False)
    source_material = OptionSerializer(many=True, required=False)
    assistant = OptionSerializer(many=True, required=False)
    collection = OptionSerializer(
        many=True, required=False, help_text="Media collections -- files a node can talk about."
    )
    collection_index = OptionSerializer(
        many=True, required=False, help_text="Searchable indexes a node can retrieve from."
    )
    agent_tools = OptionSerializer(many=True, required=False)
    custom_actions = OptionSerializer(many=True, required=False)
    mcp_tools = OptionSerializer(many=True, required=False)
    built_in_tools = serializers.DictField(
        child=OptionSerializer(many=True), required=False, help_text="Keyed by LLM provider type."
    )
    built_in_tools_config = serializers.DictField(
        required=False, help_text="Per-provider, per-tool config field descriptors. Keyed by LLM provider type."
    )
    text_editor_autocomplete_vars_llm_node = PromptVariableSerializer(many=True, required=False)
    text_editor_autocomplete_vars_router_node = PromptVariableSerializer(many=True, required=False)
    jinja_node = PromptVariableSerializer(many=True, required=False)
    default_llm_provider = DefaultLlmProviderSerializer(
        required=False, help_text="A provider/model pair that already satisfies the `must_match` rule."
    )


# --------------------------------------------------------------------------------------------
# Node type reshaping
# --------------------------------------------------------------------------------------------


def _output_topology(schema: dict) -> dict:
    """How edges leave this node type.

    Read from the node class rather than inferred from the schema: "has a `keywords` param" happens
    to identify today's routers but is not what makes a node one. Every listed type is addable, and
    the only terminating type (``EndNode``) is not, so there is no zero-output case to handle.
    """
    node_class = resolve_node_class(schema["title"])
    if node_class is not None and issubclass(node_class, PipelineRouterNode):
        return PER_KEYWORD_OUTPUT
    return SINGLE_OUTPUT


def _agent_property(name: str, prop: dict) -> dict:
    """One node param, in agent vocabulary: `ui:` keys translated or dropped, links made explicit."""
    translated = {
        UI_KEY_TRANSLATIONS[key]: value
        for key, value in prop.items()
        if key in UI_KEY_TRANSLATIONS and value is not None
    }
    plain = {key: value for key, value in prop.items() if not key.startswith("ui:")}
    return plain | translated | _param_links(name, prop)


def _param_links(name: str, prop: dict) -> dict:
    """The cross-param rules the builder enforces in JS and the schema never stated."""
    links = {}
    if "ui:optionsSource" not in prop and name in IMPLIED_OPTIONS_SOURCE:
        links["options_source"] = IMPLIED_OPTIONS_SOURCE[name]
    if name in MUST_MATCH:
        links["must_match"] = MUST_MATCH[name]
    if name in OPTIONS_KEYED_BY:
        links["options_keyed_by"] = OPTIONS_KEYED_BY[name]
    return links


def _documentation_url(schema: dict) -> str | None:
    """The node's help link, absolutised.

    ``ui:documentation_link`` is a site-relative path that the builder joins to
    ``window.DOCUMENTATION_BASE_URL`` in the browser (see ``getDocumentationLink`` in
    assets/javascript/apps/pipeline/utils.tsx). An API client has no such base, so the join happens
    here.
    """
    link = schema.get("ui:documentation_link")
    if not link:
        return None
    if link.startswith("http"):
        return link
    return f"{settings.DOCUMENTATION_BASE_URL}{link}"


@cache
def _node_types() -> list[dict]:
    """Node types reshaped for agent consumption.

    Cached because the node classes are fixed at import time, so this is static per deploy. The
    cache also captures ``DOCUMENTATION_BASE_URL``, which is deployment-static for the same reason;
    a test that overrides it needs ``_node_types.cache_clear()``.
    """
    node_types = []
    for schema in get_node_schemas():
        if not schema.get("ui:can_add"):
            # Covers both the deprecated types and the structural ones the server manages
            # (`ui:can_add` is forced False by the deprecation decorator). The endpoint answers
            # "what can I build", so a type that fails that question is not an entry with a flag on
            # it -- it is absent, and `_unknown_node_type` explains why if the agent asks directly.
            continue
        entry = {
            "type": schema["title"],
            "description": schema["description"],
            "outputs": _output_topology(schema),
            "schema": {
                key: value for key, value in schema.items() if not key.startswith("ui:") and key != "properties"
            },
        }
        entry["schema"]["properties"] = {
            name: _agent_property(name, prop) for name, prop in schema["properties"].items()
        }
        if documentation_url := _documentation_url(schema):
            entry["documentation_url"] = documentation_url
        node_types.append(entry)
    return node_types


@cache
def _deprecation_messages() -> dict[str, str]:
    """Replacement advice per deprecated type, so a 404 on one can say more than "unknown"."""
    return {
        schema["title"]: schema.get("ui:deprecation_message", "")
        for schema in get_node_schemas()
        if schema.get("ui:deprecated")
    }


@cache
def _structural_types() -> frozenset[str]:
    """Types the server creates and manages: ``StartNode``, ``EndNode``, ``Passthrough``.

    Unlisted, but ``/inspect/`` still reports them as the ``type`` of real nodes, so a lookup on one
    is a reasonable thing for an agent to do and must not come back as "unknown".
    """
    return frozenset(
        schema["title"]
        for schema in get_node_schemas()
        if not schema.get("ui:can_add") and not schema.get("ui:deprecated")
    )


def _valid_type_names() -> list[str]:
    return [node["type"] for node in _node_types()]


def _unknown_node_type(requested_type: str) -> NotFound:
    """A 404 the agent can act on: why the name failed, and what it could have asked for instead."""
    if (message := _deprecation_messages().get(requested_type)) is not None:
        advice = f" {message}" if message else ""
        detail = f"Node type '{requested_type}' is deprecated and can no longer be used.{advice}"
    elif requested_type in _structural_types():
        detail = (
            f"Node type '{requested_type}' is managed by the server and cannot be created or "
            f"configured. It may appear as a node's `type` in /inspect/ responses."
        )
    else:
        detail = f"Unknown node type: {requested_type}"
    return NotFound({"detail": detail, "valid_types": _valid_type_names()})


def _etag(payload) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return f'W/"{digest[:32]}"'


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


# --------------------------------------------------------------------------------------------
# Options reshaping
# --------------------------------------------------------------------------------------------


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


# The option keys holding prompt/template variables rather than resource ids.
PROMPT_VAR_OPTION_KEYS = (
    OptionsSource.text_editor_autocomplete_vars_llm_node,
    OptionsSource.text_editor_autocomplete_vars_router_node,
    OptionsSource.jinja_node,
)


def _describe_prompt_vars(options: dict) -> dict:
    """Swap each prompt variable's redundant ``value`` for a description of what it does.

    The builder emits these as ``{"label": v, "value": v}`` -- the two are always identical, since
    the value is just the name typed into the template. A human reading an autocomplete dropdown
    infers the rest from the name; an agent cannot, so it gets the description instead. Mutates a
    copy of the list, never ``PROMPT_VAR_DESCRIPTIONS``.
    """
    for key in PROMPT_VAR_OPTION_KEYS:
        if not (entries := options.get(key)):
            continue
        options[key] = [
            {"label": entry["label"], "description": PROMPT_VAR_DESCRIPTIONS[entry["label"]]} for entry in entries
        ]
    return options


def _rename_option_keys(options: dict) -> dict:
    return {OPTIONS_KEY_RENAMES.get(key, key): value for key, value in options.items()}


@cache
def _keys_for_node_type(node_type: str) -> frozenset[str] | None:
    """The option keys a single node type can reference, or ``None`` if no such type is served.

    Everything else in the payload belongs to some other node type, and an agent configuring this
    one pays for it in context for nothing. A known type that references nothing -- ``CodeNode``,
    the structural nodes -- yields an empty set, which is a different answer from ``None``.
    """
    entry = next((node for node in _node_types() if node["type"] == node_type), None)
    if entry is None:
        return None
    properties = entry["schema"]["properties"]
    keys = {prop["options_source"] for prop in properties.values() if prop.get("options_source")}
    if "llm_provider_id" in properties:
        keys.add("default_llm_provider")
    return frozenset(keys)


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
