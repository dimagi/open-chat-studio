import dataclasses
from collections import defaultdict

from django.apps import apps as global_apps
from django.db import connection, transaction
from pydantic import BaseModel

from apps.pipelines.models import Node
from apps.service_providers.llm_service.model_parameters import (
    AnthropicReasoningParameters,
    BasicParameters,
    ClaudeOpus46Parameters,
    ClaudeOpus47Parameters,
    ClaudeOpus55Parameters,
    ClaudeSonnet46Parameters,
    GPT5Parameters,
    GPT5ProParameters,
    GPT6Parameters,
    GPT6SolParameters,
    GPT51Parameters,
    GPT52Parameters,
    GPT55Parameters,
    OpenAIReasoningParameters,
)
from apps.service_providers.models import EmbeddingProviderModel, LlmProviderModel, LlmProviderTypes
from apps.utils.deletion import get_related_objects


@dataclasses.dataclass
class Model:
    name: str
    token_limit: int
    is_default: bool = False
    deprecated: bool = False
    is_translation_default: bool = False
    parameters: type[BaseModel] = BasicParameters
    replacement: str | None = None


def k(n: int) -> int:
    return n * 1024


DEFAULT_LLM_PROVIDER_MODELS = {
    "azure": [
        Model("o4-mini", 200000, parameters=OpenAIReasoningParameters),
        Model("o3", 200000, parameters=OpenAIReasoningParameters),
        Model("o3-mini", 200000, parameters=OpenAIReasoningParameters),
        # Token limits are the total context window Microsoft publishes for the deployment, not the
        # separate input-only figure it also lists.
        Model("gpt-6-astra", 1050000, parameters=GPT6Parameters),
        Model("gpt-5.6-terra", 1050000, parameters=GPT52Parameters),
        Model("gpt-5.6-sol", 1050000, parameters=GPT52Parameters),
        Model("gpt-5.6-luna", 1050000, parameters=GPT52Parameters),
        Model("gpt-5.5", 1050000, parameters=GPT55Parameters),
        Model("gpt-5.4", 1050000, parameters=GPT52Parameters),
        Model("gpt-5.4-pro", 1050000, parameters=GPT5ProParameters),
        Model("gpt-5.4-mini", 400000, parameters=GPT52Parameters),
        Model("gpt-5.4-nano", 400000, parameters=GPT52Parameters),
        Model("gpt-5.2", 400000, parameters=GPT52Parameters),
        Model("gpt-5.1", k(400), parameters=GPT51Parameters),
        Model("gpt-4.1", 1000000, is_translation_default=True),
        Model("gpt-4.1-mini", 1000000, is_default=True),
        Model("gpt-4.1-nano", 1000000, deprecated=True, replacement="gpt-4.1-mini"),
        Model("gpt-4o-mini", 128000),
        Model("gpt-4o", 128000),
    ],
    "anthropic": [
        Model("claude-opus-5-5", 1000000, parameters=ClaudeOpus55Parameters),
        Model("claude-opus-5", k(1000), parameters=ClaudeOpus47Parameters),
        Model("claude-sonnet-5", k(1000), parameters=ClaudeSonnet46Parameters),
        Model("claude-fable-5-1", k(1000), parameters=ClaudeOpus47Parameters),
        Model("claude-fable-5", k(1000), parameters=ClaudeOpus47Parameters),
        Model("claude-opus-4-8", k(1000), parameters=ClaudeOpus47Parameters),
        Model("claude-opus-4-7", k(1000), parameters=ClaudeOpus47Parameters),
        Model("claude-opus-4-6", k(200), is_translation_default=True, parameters=ClaudeOpus46Parameters),
        Model("claude-sonnet-4-6", 1000000, is_default=True, parameters=ClaudeSonnet46Parameters),
        Model("claude-sonnet-4-5-20250929", k(200), parameters=AnthropicReasoningParameters),
        Model("claude-haiku-4-5-20251001", k(200), parameters=AnthropicReasoningParameters),
        Model("claude-opus-4-5-20251101", k(200), parameters=AnthropicReasoningParameters),
    ],
    "openai": [
        Model("o4-mini", 200000, parameters=OpenAIReasoningParameters),
        Model("gpt-4.1", 1000000, is_translation_default=True),
        Model("gpt-4.1-mini", 1000000, is_default=True),
        Model("gpt-4.1-nano", 1000000, deprecated=True, replacement="gpt-4.1-mini"),
        Model("o3", 128000, parameters=OpenAIReasoningParameters),
        Model("o3-mini", 128000, parameters=OpenAIReasoningParameters),
        Model("gpt-4o-mini", 128000),
        Model("gpt-4o", 128000),
        Model("gpt-4", k(8)),
        Model("gpt-3.5-turbo", k(16), deprecated=True, replacement="gpt-4.1-mini"),
        Model("gpt-3.5-turbo-1106", k(16), deprecated=True),
        Model("gpt-5", k(400), deprecated=True, replacement="gpt-5.4", parameters=GPT5Parameters),
        Model("gpt-5.1", k(400), parameters=GPT51Parameters),
        Model("gpt-5.2", k(400), parameters=GPT52Parameters),
        Model("gpt-5.2-pro", k(400), parameters=GPT52Parameters),
        Model("gpt-5.4", 1050000, parameters=GPT52Parameters),
        Model("gpt-5.4-pro", 1050000, parameters=GPT5ProParameters),
        Model("gpt-5.4-mini", 400000, parameters=GPT52Parameters),
        Model("gpt-5.4-nano", 400000, parameters=GPT52Parameters),
        Model("gpt-5.5", 1050000, parameters=GPT55Parameters),
        Model("gpt-5.6-terra", 1050000, parameters=GPT52Parameters),
        Model("gpt-5.6-sol", 1050000, parameters=GPT52Parameters),
        Model("gpt-5.6-luna", 1050000, parameters=GPT52Parameters),
        Model("gpt-6-astra", 1050000, parameters=GPT6Parameters),
        Model("gpt-6-sol", 1050000, parameters=GPT6SolParameters),
        Model("gpt-6-luna", 1050000, parameters=GPT6SolParameters),
        Model("gpt-5-mini", k(400), deprecated=True, replacement="gpt-5.4-mini", parameters=GPT5Parameters),
        Model("gpt-5-nano", k(400), deprecated=True, replacement="gpt-5.4-nano", parameters=GPT5Parameters),
        Model("gpt-5-pro", k(400), deprecated=True, replacement="gpt-5.4-pro", parameters=GPT5ProParameters),
    ],
    "groq": [
        # Groq publishes an odd 131,042 context window for this model, not the 131,072 its
        # siblings use.
        Model("qwen/qwen3.8-27b", 131042),
        Model("llama-3.3-70b-versatile", k(128), deprecated=True, replacement="openai/gpt-oss-120b"),
        Model("llama-3.1-8b-instant", k(128), deprecated=True, replacement="openai/gpt-oss-20b"),
        Model("openai/gpt-oss-120b", 131072, is_default=True, is_translation_default=True),
        Model("openai/gpt-oss-20b", 131072),
    ],
    "perplexity": [
        Model("sonar", 128000, is_default=True),
        Model("sonar-pro", 200000),
        Model("sonar-reasoning-pro", 128000, is_translation_default=True),
        Model("sonar-deep-research", 128000),
        Model("llama-3.1-8b-instruct", 131072),
        Model("llama-3.1-70b-instruct", 131072),
    ],
    # OpenRouter supports thousands of models; we intentionally ship no defaults.
    # Users can add models manually, and a dedicated discovery flow using the OpenRouter
    # model API (search, autocomplete, pricing) is tracked in issue #4258. Like
    # ``voyage`` (embeddings only), providers without shipped chat models are omitted
    # from this dict entirely rather than listed with an empty list.
    "deepseek": [
        # DeepSeek-V4.1-Flash (llm-stats id `deepseek-v4.1-flash`). api.deepseek.com serves it as
        # `deepseek-flash`; the two dated names below are still accepted but now route here.
        Model("deepseek-flash", 1000000),
        # llm-stats lists this model under its open-weights name (deepseek-v4-flash-0731), but
        # api.deepseek.com only serves the undated alias, which is what we have to send.
        Model("deepseek-v4-flash", 1000000, is_default=True),
        Model("deepseek-v4-pro", 1000000, is_translation_default=True),
        # Experimental vision variant of deepseek-v4-flash. Same 1M context and text rates as
        # the base model; images are tokenised by dimension and billed as input tokens.
        Model("deepseek-v4-flash-vision-exp", 1000000),
        Model("deepseek-chat", 128000, deprecated=True, replacement="deepseek-v4-flash"),
        Model("deepseek-reasoner", 128000, deprecated=True, replacement="deepseek-v4-flash"),
    ],
    "minimax": [
        Model("MiniMax-M3", k(1000), is_default=True),
        Model("MiniMax-M2.7", 204800),
        Model("MiniMax-M2.5", 204800),
        Model("MiniMax-M2", 200000),
    ],
    "google": [
        Model("gemini-3.8-flash", 1048576),
        Model("gemini-3.7-flash", 1048576),
        Model("gemini-3.6-flash", 1048576, is_translation_default=True),
        Model("gemini-3.5-flash", 1048576, is_default=True),
        Model("gemini-3.5-flash-lite", 1048576),
        Model("gemini-3.1-pro-preview", 1048576),
        Model("gemini-3.1-flash-lite", 1048576),
        Model("gemini-2.5-flash", 1048576, deprecated=True, replacement="gemini-3.5-flash"),
        Model("gemini-2.5-pro", 1048576, deprecated=True, replacement="gemini-3.6-flash"),
    ],
    "google_vertex_ai": [
        Model("gemini-3.8-flash", 1048576),
        Model("gemini-3.7-flash", 1048576),
        Model("gemini-3.6-flash", 1048576, is_translation_default=True),
        Model("gemini-3.5-flash", 1048576, is_default=True),
        Model("gemini-3.5-flash-lite", 1048576),
        Model("gemini-3.1-pro-preview", 1048576),
        Model("gemini-3.1-flash-lite", 1048576),
        # Google is retiring the 2.5 models on the Gemini Enterprise Agent Platform: they enter
        # Extended Lifecycle Access on 2026-10-20 and lose ELA pricing on 2027-01-28.
        Model("gemini-2.5-pro", 1048576, deprecated=True, replacement="gemini-3.6-flash"),
        Model("gemini-2.5-flash", 1048576, deprecated=True, replacement="gemini-3.5-flash"),
        Model("gemini-2.5-flash-lite", 1048576, deprecated=True, replacement="gemini-3.1-flash-lite"),
    ],
}


# This list of models is used by the `remove_deprecated_models` command. It is safe to clear the list after
# the command has been run successfully in all environments. Any models that have been in the `main` branch
# for more than 1 month are safe to remove.
DELETED_MODELS = [
    # Azure
    ("azure", "gpt-4"),
    ("azure", "gpt-4-32k"),
    ("azure", "gpt-35-turbo"),
    ("azure", "gpt-35-turbo-16k"),
    # Anthropic
    ("anthropic", "claude-3-5-sonnet-latest"),
    ("anthropic", "claude-3-opus-latest"),
    ("anthropic", "claude-2.0"),
    ("anthropic", "claude-2.1"),
    ("anthropic", "claude-instant-1.2"),
    ("anthropic", "claude-sonnet-4-20250514", "claude-sonnet-4-6"),
    ("anthropic", "claude-3-5-haiku-latest", "claude-haiku-4-5-20251001"),
    ("anthropic", "claude-3-7-sonnet-20250219", "claude-sonnet-4-6"),
    ("anthropic", "claude-opus-4-20250514", "claude-opus-4-8"),
    # OpenAI
    ("openai", "o1-preview"),
    ("openai", "o1-mini"),
    ("openai", "gpt-4-turbo", "gpt-4.1"),
    ("openai", "gpt-4-turbo-preview", "gpt-4.1"),
    ("openai", "gpt-4-0125-preview", "gpt-4.1"),
    ("openai", "gpt-4-1106-preview", "gpt-4.1"),
    ("openai", "gpt-4-0613", "gpt-4.1"),
    # o4-mini-high is a ChatGPT effort preset, not an API model: it is absent from OpenAI's
    # catalogue and from its deprecation table, so requests with it already 404. References move
    # to gpt-5.6-terra, the successor OpenAI names for o4-mini, rather than to o4-mini itself,
    # which shuts down on 2026-10-23.
    ("openai", "o4-mini-high", "gpt-5.6-terra"),
    ("openai", "gpt-5.3", "gpt-5.4"),
    ("openai", "gpt-5.3-instant", "gpt-5.4-mini"),
    # OpenAI names gpt-5.1-chat-latest as the successor, but that is a rolling alias; references
    # move to gpt-5.1, the pinned model behind it that OCS registers.
    ("openai", "chatgpt-4o-latest", "gpt-5.1"),
    # Groq
    ("groq", "whisper-large-v3"),
    # Speech-to-text, billed per audio-hour: it has no token rates and cannot serve a chat
    # request, so any reference to it is already broken. References move to the Groq default.
    ("groq", "whisper-large-v3-turbo", "openai/gpt-oss-120b"),
    ("groq", "llama3-groq-70b-8192-tool-use-preview"),
    ("groq", "llama3-groq-8b-8192-tool-use-preview"),
    ("groq", "llama-3.1-70b-versatile"),
    ("groq", "llama-3.2-1b-preview"),
    ("groq", "llama-3.2-3b-preview"),
    ("groq", "llama-3.2-11b-vision-preview"),
    ("groq", "llama-3.2-90b-vision-preview"),
    ("groq", "llama-guard-3-8b"),
    ("groq", "llama3-70b-8192"),
    ("groq", "llama3-8b-8192"),
    ("groq", "mixtral-8x7b-32768"),
    ("groq", "gemma2-9b-it", "openai/gpt-oss-20b"),
    # Groq retired gemma-7b-it in favour of gemma2-9b-it, itself already deleted, so references
    # follow that chain to the Groq small-model default.
    ("groq", "gemma-7b-it", "openai/gpt-oss-20b"),
    # Perplexity
    ("perplexity", "sonar-reasoning"),
    ("perplexity", "llama-3.1-sonar-small-128k-online"),
    ("perplexity", "llama-3.1-sonar-large-128k-online"),
    ("perplexity", "llama-3.1-sonar-huge-128k-online"),
    ("perplexity", "llama-3.1-sonar-small-128k-chat", "sonar"),
    ("perplexity", "llama-3.1-sonar-large-128k-chat", "sonar-pro"),
    # Google
    ("google", "gemini-1.5-flash"),
    ("google", "gemini-1.5-flash-8b"),
    ("google", "gemini-1.5-pro"),
    ("google", "gemini-2.0-flash", "gemini-3.5-flash"),
    # Google Vertex AI
    ("google_vertex_ai", "gemini-3-pro-preview", "gemini-3.1-pro-preview"),
]


DEFAULT_EMBEDDING_PROVIDER_MODELS = {
    "openai": ["text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002"],
    "google": ["gemini-embedding-001"],
    "voyage": [
        "voyage-4-large",
        "voyage-4",
        "voyage-4-lite",
        "voyage-code-3",
        "voyage-finance-2",
        "voyage-law-2",
    ],
}


LLM_MODEL_PARAMETERS = {}
for models in DEFAULT_LLM_PROVIDER_MODELS.values():
    for model in models:
        if model.parameters:
            LLM_MODEL_PARAMETERS[model.name] = model.parameters


def get_model_parameters(model_name: str, **param_overrides) -> dict:
    """Return the model parameters, with any overrides applied."""
    parameters_model = LLM_MODEL_PARAMETERS.get(model_name, BasicParameters)
    filtered = parameters_model.filter_overrides(param_overrides)
    return parameters_model(**filtered).model_dump()


def get_default_model(provider_type: str) -> Model | None:
    """The provider's default model, or None if it has no default.

    Not every ``LlmProviderTypes`` member has chat models here (``voyage`` is embeddings
    only), so a missing provider is a legitimate miss rather than a programming error.
    """
    return next((m for m in DEFAULT_LLM_PROVIDER_MODELS.get(provider_type, ()) if m.is_default), None)


def get_deprecated_models() -> dict[tuple[str, str], str | None]:
    """``{(provider_type, model_name): replacement}`` for every deprecated model."""
    return {
        (provider_type, model.name): model.replacement
        for provider_type, models in DEFAULT_LLM_PROVIDER_MODELS.items()
        for model in models
        if model.deprecated
    }


def get_default_translation_models_by_provider() -> dict:
    """
    Returns a dict mapping provider labels (e.g., "OpenAI") to their default translation model name.
    """

    defaults = {}
    for provider_type, models in DEFAULT_LLM_PROVIDER_MODELS.items():
        default_model = next((m for m in models if m.is_translation_default), None)
        if default_model:
            provider_label = str(LlmProviderTypes[provider_type].label)
            defaults[provider_label] = default_model.name
    return defaults


@transaction.atomic()
def update_llm_provider_models():
    _update_llm_provider_models(LlmProviderModel)


@transaction.atomic()
def update_embedding_provider_models():
    _update_embedding_provider_models(EmbeddingProviderModel)


def _update_embedding_provider_models(EmbeddingProviderModel):
    """
    This method updates the EmbeddingProviderModel objects in the database to match the
    DEFAULT_EMBEDDING_PROVIDER_MODELS.
    """
    for provider_type, provider_models in DEFAULT_EMBEDDING_PROVIDER_MODELS.items():
        for model in provider_models:
            EmbeddingProviderModel.objects.get_or_create(team=None, name=model, type=provider_type)


def _update_llm_provider_models(LlmProviderModel):
    """
    This method updates the LlmProviderModel objects in the database to match the DEFAULT_LLM_PROVIDER_MODELS.
    If a model exists in the database that is not in DEFAULT_LLM_PROVIDER_MODELS, it is deleted.
    If a model exists in DEFAULT_LLM_PROVIDER_MODELS that is not in the database, it is created.

    Any references to models that are going to be deleted are updated to reference a custom model (which is created
    if it does not already exist).
    """
    existing = {(m.type, m.name): m for m in LlmProviderModel.objects.filter(team=None)}
    existing_custom_by_team = {
        (m.team_id, m.type, m.name): m for m in LlmProviderModel.objects.filter(team__isnull=False)
    }
    existing_custom_global = defaultdict(list)
    for m in existing_custom_by_team.values():
        existing_custom_global[(m.type, m.name)].append(m)

    created_models = {}
    for provider_type, provider_models in DEFAULT_LLM_PROVIDER_MODELS.items():
        for model in provider_models:
            key = (provider_type, model.name)
            if key in existing:
                _update_existing_global_model(existing.pop(key), model)
            elif not model.deprecated:
                created_models[key] = LlmProviderModel.objects.create(
                    team=None,
                    type=provider_type,
                    name=model.name,
                    max_token_limit=model.token_limit,
                )

    # replace existing custom models with the new global model and delete the custom models
    for key, model in created_models.items():
        for custom_model in existing_custom_global.get(key, []):
            _replace_custom_model_with_global(custom_model, model, LlmProviderModel)


def _update_existing_global_model(existing_global_model, model):
    """Sync an existing global model's token limit and deprecated flag with the defaults."""
    if (
        existing_global_model.max_token_limit != model.token_limit
        or existing_global_model.deprecated != model.deprecated
    ):
        existing_global_model.max_token_limit = model.token_limit
        existing_global_model.deprecated = model.deprecated
        existing_global_model.save()


def _evaluator_provider_model_fk_in_db() -> bool:
    """Whether ``Evaluator.llm_provider_model_id`` exists as a column in the database.

    Asked by introspection rather than the migration recorder because what matters is
    whether the FK constraint is live, not which migration installed it.
    """
    evaluator_table = global_apps.get_model("evaluations", "Evaluator")._meta.db_table
    with connection.cursor() as cursor:
        columns = connection.introspection.get_table_description(cursor, evaluator_table)
    return any(column.name == "llm_provider_model_id" for column in columns)


def _repoint_evaluators(custom_model, global_model) -> None:
    """Move every evaluator on ``custom_model`` to ``global_model``.

    The caller's generic FK pass would repoint them too. This exists for the guard below:
    it also runs from migrations (``migration_utils.llm_model_migration``), where in states
    older than ``evaluations.0018`` the ``evaluators`` accessor does not exist at all, and
    the generic pass cannot tell that apart from having nothing to repoint.
    """
    evaluators = getattr(custom_model, "evaluators", None)
    if evaluators is None:
        # No accessor means this migration state predates ``evaluations.0018``. Skipping is
        # only safe while the FK column is absent from the database too — once it exists the
        # constraint is live even though this state cannot see it, and the caller's delete
        # would fail on a deferred FK violation at commit rather than here.
        if _evaluator_provider_model_fk_in_db():
            raise RuntimeError(
                f"Cannot repoint evaluators off LlmProviderModel {custom_model.type}/{custom_model.name}: "
                "evaluations.Evaluator is absent from this migration's app state, but its "
                "llm_provider_model FK is already live in the database, so deleting the model would "
                "violate it. Add ('evaluations', '0018_evaluator_llm_provider_fks') to the dependencies "
                "of the migration calling llm_model_migration()."
            )
        return

    evaluators.update(llm_provider_model_id=global_model.id)


def _repoint_pipeline_nodes(custom_model, global_model) -> None:
    """Move every pipeline node on ``custom_model`` to ``global_model``.

    Written through params, which ``set_params`` mirrors onto the FK column; writing the column
    alone leaves the stale id in params for the next ``create_new_version`` to re-derive from.
    Queried off the real ``Node`` model because ``custom_model`` is historical when this runs
    from a migration, and historical rows have no ``set_params``.
    """
    for node in Node.objects.filter(llm_provider_model_id=custom_model.id):
        _update_pipeline_node_param(node, "llm_provider_model_id", global_model.id)


def _replace_custom_model_with_global(custom_model, global_model, LlmProviderModel):
    """Repoint everything referencing ``custom_model`` at ``global_model``, then delete it."""
    # Evaluators first: the generic pass below cannot tell an absent relation (an old
    # migration state) from one with nothing to repoint. Once done they drop out of it.
    _repoint_evaluators(custom_model, global_model)
    # Nodes before the generic pass too: handed historical models it repoints their FK column
    # directly, which would leave the deleted model's id in params with nothing left to find.
    _repoint_pipeline_nodes(custom_model, global_model)

    for obj in get_related_objects(custom_model):
        fields = [f for f in obj._meta.fields if f.related_model == LlmProviderModel]
        if not fields:
            # Pipelines surfaced via the Node.llm_provider_model reverse FK have no
            # direct field to repoint here; their nodes are handled by
            # ``_repoint_pipeline_nodes`` above.
            continue
        field = fields[0]
        setattr(obj, field.attname, global_model.id)
        obj.save(update_fields=[field.name])

    custom_model.delete()


def _get_or_create_custom_model(team_object, key, global_model, existing_custom_by_team):
    """Check the `existing_custom_by_team` mapping for a custom model for the given team and key.
    If one does not exist, create a new custom model and add it to the mapping.
    Return the custom model (existing or new)
    """

    id_key = (team_object.team_id, *key)
    custom_model = existing_custom_by_team.get(id_key)
    if not custom_model:
        custom_model = LlmProviderModel.objects.create(
            team_id=team_object.team_id,
            type=global_model.type,
            name=global_model.name,
            max_token_limit=global_model.max_token_limit,
        )
        existing_custom_by_team[id_key] = custom_model
    return custom_model


def _update_pipeline_node_param(node, param_name, param_value):
    # Node rows own node params (ADR-0046); pipeline.data holds layout only.
    node.params[param_name] = param_value
    node.set_params(node.params)
