"""Serializers describing the discovery endpoint responses for the OpenAPI schema.

Documentation only -- the views build plain dicts and hand them back. Each field's ``help_text`` is
what a client reads about that field.
"""

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


class ResourceOptionSerializer(serializers.Serializer):
    """One of the team's stored resources. Write `value` into the param; `label` is for humans
    reading a diff."""

    value = serializers.IntegerField(help_text="The resource's id. Write this into the node param.")
    label = serializers.CharField(help_text="What the resource is called.")


class ToolOptionSerializer(serializers.Serializer):
    """One selectable tool. Write `value` into the param; `label` is for humans reading a diff."""

    value = serializers.CharField(
        help_text=(
            "Write this into the node param. Opaque -- copy it verbatim and never construct one. "
            "`custom_actions` values in particular are composite identifiers rather than names."
        )
    )
    label = serializers.CharField()


class ProviderOptionSerializer(serializers.Serializer):
    """One of the team's configured service providers."""

    value = serializers.IntegerField(help_text="The provider's id. Write this into the node param.")
    label = serializers.CharField(help_text="The name the team gave the provider.")
    type = serializers.CharField(
        help_text="What the provider talks to -- `openai`, `anthropic`, `aws`. The join key for `must_match`."
    )


class LlmProviderModelOptionSerializer(serializers.Serializer):
    """A model the team can call, given the providers it holds."""

    value = serializers.IntegerField(help_text="The model's id. Write this into the node param.")
    label = serializers.CharField()
    type = serializers.CharField(
        help_text="The provider type that serves the model. Must equal the `type` of the chosen `llm_provider_id`."
    )
    max_token_limit = serializers.IntegerField(
        required=False, help_text="The model's context window. Absent where none is recorded."
    )


class SyntheticVoiceOptionSerializer(serializers.Serializer):
    """A voice, and the voice provider that speaks it."""

    value = serializers.IntegerField(help_text="The voice's id. Write this into the node param.")
    label = serializers.CharField()
    type = serializers.CharField(help_text="The service the voice comes from -- `aws`, `azure`, `openai`.")
    provider_id = serializers.IntegerField(
        allow_null=True,
        help_text=(
            "The `voice_provider_id` option this voice belongs to, or null for a shared voice that "
            "any provider of the same `type` can speak."
        ),
    )


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

    llm_provider_id = ProviderOptionSerializer(many=True, required=False)
    llm_provider_model_id = LlmProviderModelOptionSerializer(many=True, required=False)
    voice_provider_id = ProviderOptionSerializer(
        many=True,
        required=False,
        help_text=(
            "The team's configured voice providers. No node param sources its options from this -- "
            "it is here to resolve the `provider_id` on a `synthetic_voice_id` entry."
        ),
    )
    synthetic_voice_id = SyntheticVoiceOptionSerializer(many=True, required=False)
    source_material = ResourceOptionSerializer(many=True, required=False)
    collection = ResourceOptionSerializer(
        many=True, required=False, help_text="Media collections -- files a node can talk about."
    )
    collection_index = ResourceOptionSerializer(
        many=True, required=False, help_text="Searchable indexes a node can retrieve from."
    )
    agent_tools = ToolOptionSerializer(many=True, required=False)
    custom_actions = ToolOptionSerializer(many=True, required=False)
    built_in_tools = serializers.DictField(
        child=ToolOptionSerializer(many=True), required=False, help_text="Keyed by LLM provider type."
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
