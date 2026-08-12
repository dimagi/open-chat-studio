"""Serializers describing the discovery endpoint responses for the OpenAPI schema.

Documentation only -- the views build plain dicts and hand them back. Each field's ``help_text`` is
what a client reads about that field.
"""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers


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
            "JSON Schema for the node's `params`. A param drawing from a fixed set of values takes "
            "them from the `/pipeline/options/` key of the same name. Properties carry these keys "
            "beyond standard JSON Schema, where they apply: `must_match` (this value must agree "
            "with another param's chosen option on the named attribute), `options_keyed_by` (the "
            "option list is a dict, and another param's chosen option selects the sub-list), "
            "`applies_when` (the param is ignored unless the condition holds) and "
            "`requires_feature_flag`."
        )
    )


class NodeTypeNotFoundSerializer(serializers.Serializer):
    """The body DRF renders for the `NotFound` raised on an unknown `type`."""

    detail = serializers.CharField()
    valid_types = serializers.ListField(
        child=serializers.CharField(), help_text="Every type this endpoint serves, so a failed call can be corrected."
    )


@extend_schema_field({"oneOf": [{"type": "string"}, {"type": "integer"}]})
class OptionValueField(serializers.Field):
    """An option's ``value``: an integer for the model-backed keys, a string elsewhere.

    Passes the value through untouched -- a typed field would coerce one into the other.
    """

    def to_representation(self, value):
        return value


class OptionSerializer(serializers.Serializer):
    """One selectable value. Write `value` into the param; `label` is for humans reading a diff."""

    value = OptionValueField(
        help_text=(
            "Write this into the node param. Opaque -- copy it verbatim and never construct one. "
            "An integer for the model-backed keys, a string elsewhere; `custom_actions` values in "
            "particular are composite string identifiers."
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
    """The documented keys. Each holds the values for the node param of the same name. A response
    carries a subset when `?node_type=` is given, and may carry keys not listed here as new node
    params are added."""

    llm_provider_id = OptionSerializer(many=True, required=False)
    llm_provider_model_id = OptionSerializer(many=True, required=False)
    voice_provider_id = OptionSerializer(
        many=True,
        required=False,
        help_text="The team's configured voice providers. No node param sources its options from this.",
    )
    synthetic_voice_id = OptionSerializer(many=True, required=False)
    source_material = OptionSerializer(many=True, required=False)
    collection = OptionSerializer(
        many=True, required=False, help_text="Media collections -- files a node can talk about."
    )
    collection_index = OptionSerializer(
        many=True, required=False, help_text="Searchable indexes a node can retrieve from."
    )
    agent_tools = OptionSerializer(many=True, required=False)
    custom_actions = OptionSerializer(many=True, required=False)
    built_in_tools = serializers.DictField(
        child=OptionSerializer(many=True), required=False, help_text="Keyed by LLM provider type."
    )
    tool_config = serializers.DictField(
        required=False, help_text="Per-provider, per-tool config field descriptors. Keyed by LLM provider type."
    )
    template_variables = PromptVariableSerializer(
        many=True,
        required=False,
        help_text=(
            "The variables a Jinja param may reference, written double-braced -- `{{input}}`. Covers "
            "`RenderTemplate`'s `template_string` and `SendEmail`'s `recipient_list`, `subject` and "
            "`body`. Distinct from the two prompt lists in both content and syntax: those are "
            "single-braced. Use `?node_type=` to get the list that applies."
        ),
    )
    llm_prompt_variables = PromptVariableSerializer(
        many=True,
        required=False,
        help_text=(
            "The variables an LLM node's `prompt` may reference, written single-braced -- "
            "`{source_material}`. A superset of `router_prompt_variables`: it adds the ones backed by a "
            "param on the same node, such as `source_material` and `media`."
        ),
    )
    router_prompt_variables = PromptVariableSerializer(
        many=True,
        required=False,
        help_text=(
            "The variables a router node's `prompt` may reference, written single-braced. Narrower "
            "than `llm_prompt_variables` -- a router has no source material or media to interpolate."
        ),
    )
    default_llm_provider = DefaultLlmProviderSerializer(
        required=False, help_text="A provider/model pair that already satisfies the `must_match` rule."
    )
