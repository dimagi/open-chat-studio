"""Node type schemas and the option values each node param accepts.

Shared by the pipeline builder views and the v2 discovery API. The builder renders these straight
into its editor; the API reshapes them in ``apps/api/v2/discovery/``. Keep this module free of request
and UI concerns so both callers can use it.
"""

import inspect
from collections import defaultdict
from collections.abc import Callable, Iterable

from django.db.models import QuerySet
from django.db.models.functions import Lower
from django.urls import reverse

from apps.assistants.models import OpenAiAssistant
from apps.custom_actions.form_utils import get_custom_action_operation_choices
from apps.custom_actions.models import CustomAction
from apps.documents.models import Collection
from apps.experiments.models import AgentTools, BuiltInTools, SourceMaterial, SyntheticVoice
from apps.pipelines.nodes import nodes as pipeline_nodes
from apps.pipelines.nodes.base import OptionsSource
from apps.service_providers.models import LlmProvider, LlmProviderModel, VoiceProvider
from apps.teams.models import Team
from apps.utils.fields import as_int
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


def usable_llm_provider_models(team: Team) -> QuerySet:
    """The LLM models `team` can actually call: its own or shared with every team, of a type it holds
    a provider for, and not deprecated."""
    provider_types = set(LlmProvider.objects.filter(team=team).values_list("type", flat=True))
    return _usable_models(LlmProviderModel.objects.for_team(team), provider_types)


def team_source_materials(team: Team, include_versions: bool = False) -> QuerySet:
    """The source material `team` may reference."""
    return SourceMaterial.objects.filter(**_resource_filters(team, include_versions))


def team_assistants(team: Team, include_versions: bool = False) -> QuerySet:
    """The assistants `team` may reference."""
    return OpenAiAssistant.objects.filter(**_resource_filters(team, include_versions))


def team_collections(team: Team, include_versions: bool = False) -> QuerySet:
    """The media collections `team` may reference. Indexes are a separate list -- see
    :func:`team_collection_indexes` -- though both are ``Collection`` rows."""
    return Collection.objects.filter(**_resource_filters(team, include_versions), is_index=False)


def team_collection_indexes(team: Team, include_versions: bool = False) -> QuerySet:
    """The searchable indexes `team` may reference."""
    return Collection.objects.filter(**_resource_filters(team, include_versions), is_index=True)


def _resource_filters(team: Team, include_versions: bool) -> dict:
    """Scoping shared by every versioned team resource. Excluding the versions is the default: a
    version belongs to a published pipeline, not to something a caller may point a new node at."""
    filters = {"team": team}
    if not include_versions:
        filters["working_version"] = None
    return filters


def _usable_models(queryset: QuerySet, provider_types: set[str]) -> QuerySet:
    return queryset.filter(type__in=provider_types, deprecated=False)


def _team_llm_options(team: Team, usable_models_only: bool) -> tuple[list[dict], QuerySet]:
    """The team's LLM providers and the models it may pair them with.

    `usable_models_only` keeps just the models the team can actually call. A model with no team is
    shared with every team, so the provider type is the only thing that scopes it; a deprecated one is
    dropped rather than flagged.
    """
    llm_providers = list(LlmProvider.objects.filter(team=team).values("id", "name", "type"))
    llm_provider_models = LlmProviderModel.objects.for_team(team)
    if usable_models_only:
        llm_provider_models = _usable_models(llm_provider_models, {provider["type"] for provider in llm_providers})
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


def _llm_provider_options(llm_providers: list[dict], llm_provider_models: QuerySet) -> dict:
    return {
        OptionsSource.llm_provider_id: [
            _option(provider["id"], provider["name"], provider["type"]) for provider in llm_providers
        ],
        OptionsSource.llm_provider_model_id: [
            _option(provider.id, str(provider), provider.type, None, provider.max_token_limit)
            for provider in llm_provider_models
        ],
    }


def _team_resource_options(team: Team, include_versions: bool) -> dict:
    """The team's referenceable resources, each with a link into the UI for the builder's edit button."""
    source_materials = team_source_materials(team, include_versions).values("id", "topic")
    assistants = team_assistants(team, include_versions).values("id", "name")
    collections = team_collections(team, include_versions).values("id", "name")
    collection_indexes = team_collection_indexes(team, include_versions).values("id", "name", "is_remote_index")

    def _collection_url(collection_id: int) -> str:
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


def _tool_options(team: Team) -> dict:
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


def _built_in_tool_options(llm_providers: list[dict]) -> dict:
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


def _prompt_var_options() -> dict:
    return {
        OptionsSource.llm_prompt_variables: PromptVars.get_all_prompt_vars(),
        OptionsSource.router_prompt_variables: PromptVars.get_router_prompt_vars(),
        OptionsSource.template_variables: PromptVars.get_jinja_vars(),
    }


def _synthetic_voice_options(synthetic_voices: QuerySet | list[SyntheticVoice]) -> dict:
    return {
        OptionsSource.synthetic_voice_id: sorted(
            [
                _option(voice.id, str(voice), voice.service.lower()) | {"provider_id": voice.voice_provider_id}
                for voice in synthetic_voices
            ],
            key=lambda v: v["label"],
        )
    }


def _option(
    value: int | str,
    label: str,
    type_: str | None = None,
    edit_url: str | None = None,
    max_token_limit: int | None = None,
) -> dict:
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


def get_node_schemas() -> list[dict]:
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


def _get_node_schema(node_class: type) -> dict:
    schema = resolve_references(node_class.model_json_schema())
    schema.pop("$defs", None)
    collapse_optional_types(schema)
    return schema


# --------------------------------------------------------------------------------------------------
# Which of a set of supplied values a team may actually use, one function per option list whose values
# a team could be denied. Reached through `get_resolver`; the write API refuses a param naming
# anything these do not return (see `apps.api.v2.pipeline_edit.references`).
#
# They live here, beside the querysets the option lists themselves are built from, so that what a
# client is offered and what a write accepts cannot come apart -- which is what
# `test_a_write_accepts_exactly_the_values_the_options_endpoint_offers` holds them to.
#
# Each asks after the values it was handed rather than building the list to pick them out of: a write
# runs this while holding the pipeline row, so the cost must not grow with the size of the team.
#
# Values arrive straight off a request body, so each has to survive being handed anything JSON can
# express -- a dict where an id belongs, a list where a name does. Nothing here may raise on one:
# the caller reports what a resolver leaves out as a 400, so raising would answer a malformed body
# with a 500 instead.
# --------------------------------------------------------------------------------------------------


def reachable_llm_providers(team: Team, values: list) -> set:
    return _rows_named_by(LlmProvider.objects.filter(team=team), values)


def reachable_llm_provider_models(team: Team, values: list) -> set:
    return _rows_named_by(usable_llm_provider_models(team), values)


def reachable_source_materials(team: Team, values: list) -> set:
    return _rows_named_by(team_source_materials(team), values)


def reachable_assistants(team: Team, values: list) -> set:
    return _rows_named_by(team_assistants(team), values)


def reachable_collections(team: Team, values: list) -> set:
    return _rows_named_by(team_collections(team), values)


def reachable_collection_indexes(team: Team, values: list) -> set:
    return _rows_named_by(team_collection_indexes(team), values)


def reachable_synthetic_voices(team: Team, values: list) -> set:
    return _rows_named_by(get_speakable_voices(team), values)


def reachable_custom_actions(team: Team, values: list) -> set:
    """Values are ``"<custom action id>:<operation id>"``.

    Owning the action is not enough on its own: the operation has to be one its schema publishes and
    one the team allowed, which is the same test ``get_custom_action_operation_choices`` applies to
    decide what to offer.
    """
    wanted: dict[int, set[str]] = defaultdict(set)
    for value in values:
        action_id, _, operation_id = value.partition(":") if isinstance(value, str) else ("", "", "")
        if operation_id and (parsed := as_int(action_id)) is not None:
            wanted[parsed].add(operation_id)
    if not wanted:
        return set()
    reachable = set()
    # Deferred to match the offer path: neither column is read here, and `api_schema` is a large
    # JSONField to be pulling over while the pipeline row is locked.
    for action in CustomAction.objects.filter(team=team, id__in=wanted).defer("api_schema", "prompt"):
        published = action.get_operations_by_id()
        reachable |= {
            f"{action.id}:{operation_id}"
            for operation_id in wanted[action.id]
            if operation_id in action.allowed_operations and operation_id in published
        }
    return reachable


def reachable_tools(_team: Team, values: list) -> set:
    """The agent tools are a fixed vocabulary rather than rows, so no team narrows them. Checked all
    the same: an unknown name would otherwise be stored and only surface when the bot ran.

    Only a string can name a tool, and asking whether an unhashable value is one would raise rather
    than answer, so anything else is simply not among them.
    """
    offered = {value for value, _label in AgentTools.user_tool_choices()}
    return {value for value in values if isinstance(value, str) and value in offered}


def _rows_named_by(queryset: QuerySet, values: list) -> set:
    """The supplied values that name a row in ``queryset``, one query however many were sent.

    Values arrive straight off a request body, so a value that is not an id at all -- a string, a
    dict, a bool -- simply names no row. ``as_int`` is what ``Node._sync_resource_fk_fields`` writes
    the columns through, so the two agree on which values are ids in the first place.
    """
    ids = {parsed for parsed in map(as_int, values) if parsed is not None}
    if not ids:
        return set()
    return set(queryset.filter(id__in=ids).values_list("id", flat=True))


#: The resolver behind each option list, keyed on the list rather than on the param, because the list
#: is what decides the permitted values -- ``source_material_id`` is the param and ``source_material``
#: the list it chooses from. ``apps.api.v2.discovery.contract.PARAMETER_OPTION_SOURCES`` is the set of
#: sources a write checks, and ``test_every_checked_param_has_a_resolver`` is what says every one of
#: those is a key here.
RESOLVERS: dict[OptionsSource, Callable[[Team, list], set]] = {
    OptionsSource.llm_provider_id: reachable_llm_providers,
    OptionsSource.llm_provider_model_id: reachable_llm_provider_models,
    OptionsSource.source_material: reachable_source_materials,
    OptionsSource.assistant: reachable_assistants,
    OptionsSource.collection: reachable_collections,
    OptionsSource.collection_index: reachable_collection_indexes,
    OptionsSource.synthetic_voice_id: reachable_synthetic_voices,
    OptionsSource.custom_actions: reachable_custom_actions,
    OptionsSource.tools: reachable_tools,
}


def get_resolver(source: OptionsSource) -> Callable[[Team, list], set]:
    """The function answering "which of these values may this team actually use?" for one option list.

    Raises for a list that can deny nothing: the prompt-variable lists document what a template may
    interpolate, and the two tool-config lists nest their options under provider types. Reaching
    here with one of those would mean something asked to check a value against a list that cannot
    refuse it, which is a bug rather than a permissive answer.
    """
    try:
        return RESOLVERS[source]
    except KeyError:
        raise NotImplementedError(f"'{source}' has no resolver: it offers nothing a team could be denied.") from None
