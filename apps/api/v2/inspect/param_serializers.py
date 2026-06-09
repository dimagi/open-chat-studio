"""Per-node-type schemas for an inspect node's ``params``.

These serializers exist only to document the ``params`` object in the OpenAPI schema — they are
never used to render data. ``InspectNodeSerializer.get_params`` still returns the node's stored
config verbatim; ``node_params_schema()`` attaches the union of these shapes as the field's schema
so each node type's real parameters (and their meanings) are visible to API consumers.

The field groups mirror the pipeline node mixins in ``apps/pipelines/nodes/mixins.py`` so the schema
tracks the node classes: ``_TagParams`` ↔ ``OutputMessageTagMixin``, ``_LlmParams`` ↔
``LLMResponseMixin``, ``_HistoryParams`` ↔ ``HistoryMixin``, ``_RouterParams`` ↔ ``RouterMixin``.
Resource ids (``llm_provider_id``, ``collection_id``, …) and ``name`` are surfaced under their own
top-level node keys, so they are intentionally absent here.
"""

from drf_spectacular.utils import PolymorphicProxySerializer, extend_schema_serializer
from rest_framework import serializers


# ── Field groups (mirror the node mixins) ────────────────────────────────────────────────────────
class _TagParams(serializers.Serializer):
    tag = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Tag applied to this node's output message; blank means no tag.",
    )


class _LlmParams(serializers.Serializer):
    llm_model_parameters = serializers.DictField(
        required=False,
        allow_null=True,
        help_text=(
            "Model call parameters (e.g. ``temperature``) keyed by name. Older nodes may instead "
            "carry a flat ``llm_temperature``; both feed the same underlying setting."
        ),
    )


class _HistoryParams(_LlmParams):
    history_type = serializers.CharField(
        required=False,
        help_text=(
            "Where this node's chat history is scoped: ``global`` (whole session), ``node`` (this "
            "node only), ``named`` (a shared, named history), or ``none``."
        ),
    )
    history_name = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Identifier of the shared history when ``history_type`` is ``named``; otherwise null.",
    )
    history_mode = serializers.CharField(
        required=False,
        help_text=(
            "How history is compressed once it exceeds the limit: ``summarize``, ``truncate_tokens``, "
            "or ``max_history_length``."
        ),
    )
    max_history_length = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Keep only the most recent messages up to this count.",
    )
    user_max_token_limit = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text=(
            "Token budget before history is summarized or truncated. Distinct from the LLM model's "
            "own context window (reported under the node's ``llm`` object)."
        ),
    )


class _RouterParams(serializers.Serializer):
    keywords = serializers.ListField(
        child=serializers.CharField(allow_blank=True),
        required=False,
        help_text=(
            "Router branch labels, one per output handle (``output_0`` maps to ``keywords[0]``, etc.). "
            "Only meaningful on router nodes; a stray empty value on other node types is legacy data."
        ),
    )
    default_keyword_index = serializers.IntegerField(
        required=False,
        help_text="Index into ``keywords`` used when no branch matches.",
    )
    tag_output_message = serializers.BooleanField(
        required=False,
        help_text="Tag the output message with the selected route.",
    )


# ── Per-node-type param shapes ─────────────────────────────────────────────────────────────────
@extend_schema_serializer(component_name="NoParams")
class _NoParams(serializers.Serializer):
    """Nodes with no configurable parameters (``StartNode``, ``EndNode``, ``Passthrough``)."""


@extend_schema_serializer(component_name="RenderTemplateParams")
class RenderTemplateParams(_TagParams):
    template_string = serializers.CharField(
        required=False, help_text="Jinja2 template rendered against the node input."
    )


@extend_schema_serializer(component_name="CodeNodeParams")
class CodeNodeParams(_TagParams):
    code = serializers.CharField(
        required=False, help_text="Python source defining a ``main(input, **kwargs)`` function."
    )


@extend_schema_serializer(component_name="LLMResponseParams")
class LLMResponseParams(_LlmParams):
    """An LLM call with no prompt/history configuration of its own."""


@extend_schema_serializer(component_name="LLMResponseWithPromptParams")
class LLMResponseWithPromptParams(_TagParams, _HistoryParams):
    prompt = serializers.CharField(required=False, help_text="System prompt for the LLM call.")
    generate_citations = serializers.BooleanField(
        required=False,
        help_text="Reference indexed-collection files in responses and let users download them.",
    )
    max_indexed_collection_search_results = serializers.IntegerField(
        required=False,
        help_text="Maximum number of results retrieved from an indexed collection search.",
    )
    tools = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_null=True,
        help_text="Names of OCS tools enabled for the node (e.g. ``update-user-data``).",
    )
    built_in_tools = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_null=True,
        help_text="Provider-native tools enabled for the model (e.g. ``web-search``).",
    )
    tool_config = serializers.DictField(
        required=False, allow_null=True, help_text="Per-tool configuration for the built-in tools."
    )
    mcp_tools = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_null=True,
        help_text="MCP server tools enabled for the node.",
    )


@extend_schema_serializer(component_name="RouterNodeParams")
class RouterNodeParams(_HistoryParams, _RouterParams):
    prompt = serializers.CharField(required=False, help_text="Prompt steering the LLM router's choice.")


@extend_schema_serializer(component_name="StaticRouterNodeParams")
class StaticRouterNodeParams(_RouterParams):
    data_source = serializers.CharField(
        required=False, help_text="Where the routing value is read from (e.g. ``participant_data``)."
    )
    route_key = serializers.CharField(
        required=False, allow_null=True, help_text="Key within the data source whose value selects the route."
    )


@extend_schema_serializer(component_name="BooleanNodeParams")
class BooleanNodeParams(serializers.Serializer):
    input_equals = serializers.CharField(
        required=False, help_text="Value the node input is compared against to pick the true/false branch."
    )
    tag_output_message = serializers.BooleanField(
        required=False, help_text="Tag the output message with the selected route."
    )


@extend_schema_serializer(component_name="SendEmailParams")
class SendEmailParams(_TagParams):
    recipient_list = serializers.CharField(
        required=False, help_text="Comma-separated email addresses. Supports Jinja2 templates."
    )
    subject = serializers.CharField(required=False, help_text="Email subject. Supports Jinja2 templates.")
    body = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Jinja2 body template; the pipeline input is used when blank.",
    )


@extend_schema_serializer(component_name="AssistantNodeParams")
class AssistantNodeParams(_TagParams):
    citations_enabled = serializers.BooleanField(required=False, help_text="Include cited sources in responses.")
    input_formatter = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Optional wrapper for the user input; use ``{input}`` as the placeholder.",
    )


@extend_schema_serializer(component_name="ExtractStructuredDataParams")
class ExtractStructuredDataParams(_TagParams, _LlmParams):
    data_schema = serializers.CharField(
        required=False,
        help_text="JSON object mapping field name to a description of the value to extract.",
    )


@extend_schema_serializer(component_name="ExtractParticipantDataParams")
class ExtractParticipantDataParams(_TagParams, _LlmParams):
    data_schema = serializers.CharField(
        required=False,
        help_text="JSON object mapping field name to a description of the value to extract.",
    )
    key_name = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Participant-data key the extracted value is stored under; blank stores at the top level.",
    )


# Node type name -> its param shape. Types absent here fall through to the generic object schema.
NODE_PARAM_SERIALIZERS = {
    "StartNode": _NoParams,
    "EndNode": _NoParams,
    "Passthrough": _NoParams,
    "RenderTemplate": RenderTemplateParams,
    "CodeNode": CodeNodeParams,
    "LLMResponse": LLMResponseParams,
    "LLMResponseWithPrompt": LLMResponseWithPromptParams,
    "RouterNode": RouterNodeParams,
    "StaticRouterNode": StaticRouterNodeParams,
    "BooleanNode": BooleanNodeParams,
    "SendEmail": SendEmailParams,
    "AssistantNode": AssistantNodeParams,
    "ExtractStructuredData": ExtractStructuredDataParams,
    "ExtractParticipantData": ExtractParticipantDataParams,
}


def node_params_schema() -> PolymorphicProxySerializer:
    """Schema-only union of every node type's param shape.

    Rendered as a ``oneOf`` (``resource_type_field_name=None``) because the discriminating node
    ``type`` lives alongside ``params`` on the node, not inside the params object itself.
    """
    unique_serializers: list[type[serializers.Serializer]] = []
    for serializer in NODE_PARAM_SERIALIZERS.values():
        if serializer not in unique_serializers:
            unique_serializers.append(serializer)
    return PolymorphicProxySerializer(
        component_name="InspectNodeParams",
        serializers=unique_serializers,
        resource_type_field_name=None,
    )
