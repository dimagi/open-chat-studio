"""Node type schemas and the option values each node param accepts.

Shared by the pipeline builder views and the v2 discovery API. The builder renders these straight
into its editor; the API reshapes them in ``apps/api/v2/discovery/``. Keep this module free of request
and UI concerns so both callers can use it.
"""

import inspect
from collections.abc import Iterable

from django.db.models import QuerySet
from django.db.models.functions import Lower
from django.urls import reverse

from apps.assistants.models import OpenAiAssistant
from apps.custom_actions.form_utils import get_custom_action_operation_choices
from apps.documents.models import Collection
from apps.experiments.models import AgentTools, BuiltInTools, SourceMaterial, SyntheticVoice
from apps.pipelines.nodes import nodes as pipeline_nodes
from apps.pipelines.nodes.base import OptionsSource
from apps.service_providers.models import LlmProvider, LlmProviderModel, VoiceProvider
from apps.teams.models import Team
from apps.utils.prompt import PromptVars
from apps.utils.schema_utils import collapse_optional_types, resolve_references


def get_node_parameter_values(
    team: Team,
    synthetic_voices: QuerySet | list[SyntheticVoice] | None = None,
    include_versions: bool = False,
    usable_models_only: bool = False,
) -> dict:
    """Returns the possible values for each input type.

    Every list is derived from `team`. The keyword arguments cover the three callers' differences:

    * `synthetic_voices`: which voices to offer, defaulting to every voice the team has a provider to
      speak. The chatbot builder narrows this to the experiment's own voice provider; the pipeline
      builder has no voice provider to narrow by and passes an empty list.
    * `include_versions`: also offer the versioned copies of the team's resources, not just the
      working versions.
    * `usable_models_only`: drop the LLM models the team cannot call. The builders keep them so a node
      already pointing at one still renders its selection; the write API omits them because they are
      not values a client may choose.
    """
    llm_providers, llm_provider_models = _team_llm_options(team, usable_models_only)
    if synthetic_voices is None:
        synthetic_voices = get_speakable_voices(team)
    return {
        **_llm_provider_options(llm_providers, llm_provider_models),
        **_team_resource_options(team, include_versions),
        **_tool_options(team),
        **_built_in_tool_options(llm_providers),
        **_prompt_var_options(),
        **_synthetic_voice_options(synthetic_voices),
    }


def _team_llm_options(team: Team, usable_models_only: bool) -> tuple[list[dict], QuerySet]:
    """The team's LLM providers and the models it may pair them with.

    `usable_models_only` keeps just the models the team can actually call. A model with no team is
    shared with every team, so the provider type is the only thing that scopes it; a deprecated one is
    dropped rather than flagged.
    """
    llm_providers = list(LlmProvider.objects.filter(team=team).values("id", "name", "type"))
    llm_provider_models = LlmProviderModel.objects.for_team(team)
    if usable_models_only:
        provider_types = {provider["type"] for provider in llm_providers}
        llm_provider_models = llm_provider_models.filter(type__in=provider_types, deprecated=False)
    return llm_providers, llm_provider_models


def get_speakable_voices(
    team: Team, voice_providers: QuerySet | list[VoiceProvider] | None = None, exclude_services: Iterable[str] = ()
) -> QuerySet:
    """The voices the team has a provider to speak, or only those `voice_providers` can speak.

    `SyntheticVoice.service` ("AWS") and the provider type ("aws") differ in case, so the match is
    made on a lowered annotation.
    """
    if voice_providers is None:
        voice_providers = VoiceProvider.objects.filter(team=team)
    reachable_services = {provider.type.lower() for provider in voice_providers}
    if not reachable_services:
        return SyntheticVoice.objects.none()
    return (
        SyntheticVoice.get_for_team(team, list(exclude_services))
        .annotate(service_lower=Lower("service"))
        .filter(service_lower__in=reachable_services)
    )


def _llm_provider_options(llm_providers: list[dict], llm_provider_models: QuerySet):
    return {
        OptionsSource.llm_provider_id: [
            _option(provider["id"], provider["name"], provider["type"]) for provider in llm_providers
        ],
        OptionsSource.llm_provider_model_id: [
            _option(provider.id, str(provider), provider.type, None, provider.max_token_limit)
            for provider in llm_provider_models
        ],
    }


def _team_resource_options(team, include_versions: bool):
    """The team's referenceable resources, each with a link into the UI for the builder's edit button."""
    common_filters = {"team": team}
    if not include_versions:
        common_filters["working_version"] = None
    source_materials = SourceMaterial.objects.filter(**common_filters).values("id", "topic").all()
    assistants = OpenAiAssistant.objects.filter(**common_filters).values("id", "name").all()
    collections = Collection.objects.filter(**common_filters).filter(is_index=False).values("id", "name").all()
    collection_indexes = (
        Collection.objects.filter(**common_filters)
        .filter(team=team, is_index=True)
        .values("id", "name", "is_remote_index")
        .all()
    )

    def _collection_url(collection_id: int):
        return reverse("documents:single_collection_home", kwargs={"team_slug": team.slug, "pk": collection_id})

    return {
        OptionsSource.source_material: (
            [_option("", "Select a topic")]
            + [_option(material["id"], material["topic"]) for material in source_materials]
        ),
        OptionsSource.assistant: (
            [_option("", "Select an Assistant")]
            + [
                _option(
                    value=assistant["id"],
                    label=assistant["name"],
                    # Always link to the working version. If `working_version_id` is None, it means the
                    # assistant is the working version.
                    edit_url=reverse("assistants:edit", args=[team.slug, assistant["id"]]),
                )
                for assistant in assistants
            ]
        ),
        OptionsSource.collection: (
            [_option("", "Select a Collection")]
            + [
                _option(
                    value=collection["id"],
                    label=collection["name"],
                    edit_url=_collection_url(collection["id"]),
                )
                for collection in collections
            ]
        ),
        OptionsSource.collection_index: [
            _option(
                value=index["id"],
                label=f"{index['name']} ({'Remote' if index['is_remote_index'] else 'Local'})",
                edit_url=_collection_url(index["id"]),
            )
            for index in collection_indexes
        ],
    }


def _tool_options(team):
    custom_action_operations = []
    for _custom_action_name, operations_disp in get_custom_action_operation_choices(team):
        custom_action_operations.extend(operations_disp)

    mcp_tools = [
        (f"{server.id}:{tool}", f"{server.name}: {tool}")
        for server in team.mcpserver_set.all()
        for tool in server.available_tools
    ]

    return {
        OptionsSource.tools: [_option(value, label) for value, label in AgentTools.user_tool_choices()],
        OptionsSource.mcp_tools: [_option(value, label) for value, label in mcp_tools],
        OptionsSource.custom_actions: [_option(val, display_val) for val, display_val in custom_action_operations],
    }


def _built_in_tool_options(llm_providers: list[dict]):
    """Built-in tools and the config fields they take, both keyed by provider type -- each provider
    exposes a different set, and a type the team holds no provider for is not offered at all."""
    provider_types = {provider["type"].lower() for provider in llm_providers if provider.get("type")}
    return {
        OptionsSource.built_in_tools: {
            provider_type: [_option(value, label) for value, label in BuiltInTools.choices_for_provider(provider_type)]
            for provider_type in provider_types
        },
        OptionsSource.tool_config: {
            provider_type: config
            for provider_type, config in BuiltInTools.get_tool_configs_by_provider().items()
            if provider_type in provider_types
        },
    }


def _prompt_var_options():
    return {
        OptionsSource.llm_prompt_variables: PromptVars.get_all_prompt_vars(),
        OptionsSource.router_prompt_variables: PromptVars.get_router_prompt_vars(),
        OptionsSource.template_variables: PromptVars.get_jinja_vars(),
    }


def _synthetic_voice_options(synthetic_voices):
    return {
        OptionsSource.synthetic_voice_id: sorted(
            [
                _option(voice.id, str(voice), voice.service.lower()) | {"provider_id": voice.voice_provider_id}
                for voice in synthetic_voices
            ],
            key=lambda v: v["label"],
        )
    }


def _option(value, label, type_=None, edit_url: str | None = None, max_token_limit=None):
    data = {"value": value, "label": label}
    data = data | ({"type": type_} if type_ else {})
    data = data | ({"edit_url": edit_url} if edit_url else {})
    # 0 is a real limit -- it disables history compression -- so only an absent one is dropped.
    data = data | ({"max_token_limit": max_token_limit} if max_token_limit is not None else {})
    return data


def get_node_default_values(team: Team, usable_models_only: bool = False) -> dict:
    """A provider and model pairing to start from, matched on type so that no node rejects it.

    `usable_models_only` carries the same meaning as in `get_node_parameter_values` -- the pair has to
    be drawn from the same list the caller offers.
    """
    llm_providers, llm_provider_models = _team_llm_options(team, usable_models_only)
    llm_provider_model = None
    provider_id = None
    if len(llm_providers) > 0:
        for provider in llm_providers:
            llm_provider_model = llm_provider_models.filter(type=provider["type"]).first()
            if llm_provider_model:
                provider_id = provider["id"]
                break

    return {
        "llm_provider_id": provider_id,
        "llm_provider_model_id": llm_provider_model.id if llm_provider_model else None,
    }


def get_node_schemas():
    schemas = []

    node_classes = [
        cls
        for _, cls in inspect.getmembers(pipeline_nodes, inspect.isclass)
        if issubclass(cls, pipeline_nodes.PipelineNode | pipeline_nodes.PipelineRouterNode)
        and cls not in (pipeline_nodes.PipelineNode, pipeline_nodes.PipelineRouterNode)
    ]
    for node_class in node_classes:
        schemas.append(_get_node_schema(node_class))

    return schemas


def _get_node_schema(node_class):
    schema = resolve_references(node_class.model_json_schema())
    schema.pop("$defs", None)
    collapse_optional_types(schema)
    return schema
