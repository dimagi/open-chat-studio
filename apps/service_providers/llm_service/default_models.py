import dataclasses
from collections import defaultdict
from enum import Enum

from django.db import transaction

from apps.service_providers.llm_service.model_parameters import (
    AnthropicNonReasoningParameters,
    AnthropicReasoningParameters,
    OpenAIReasoningParameters,
)
from apps.utils.deletion import get_related_objects, get_related_pipelines_queryset


@dataclasses.dataclass
class Model:
    name: str
    token_limit: int
    is_default: bool = False
    deprecated: bool = False
    is_translation_default: bool = False
    parameters: Enum = None


def k(n: int) -> int:
    return n * 1024


DEFAULT_LLM_PROVIDER_MODELS = {
    "azure": [
        Model("o4-mini", 200000, parameters=OpenAIReasoningParameters),
        Model("o3", 200000, parameters=OpenAIReasoningParameters),
        Model("o3-mini", 200000, parameters=OpenAIReasoningParameters),
        Model("gpt-4.1", 1000000, is_translation_default=True),
        Model("gpt-4.1-mini", 1000000, is_default=True),
        Model("gpt-4.1-nano", 1000000),
        Model("gpt-4o-mini", 128000),
        Model("gpt-4o", 128000),
        Model("gpt-4", k(8), deprecated=True),
        Model("gpt-4-32k", 32768, deprecated=True),
        Model("gpt-35-turbo", 16385),
        Model("gpt-35-turbo-16k", 16384, deprecated=True),
    ],
    "anthropic": [
        Model("claude-sonnet-4-20250514", k(200), parameters=AnthropicReasoningParameters),
        Model("claude-opus-4-20250514", k(200), is_translation_default=True, parameters=AnthropicReasoningParameters),
        Model("claude-3-7-sonnet-20250219", k(200), parameters=AnthropicNonReasoningParameters),
        Model("claude-3-5-sonnet-latest", k(200), parameters=AnthropicNonReasoningParameters),
        Model("claude-3-5-haiku-latest", k(200), is_default=True, parameters=AnthropicNonReasoningParameters),
        Model("claude-3-opus-latest", k(200), deprecated=True, parameters=AnthropicNonReasoningParameters),
        Model("claude-2.0", k(100), deprecated=True, parameters=AnthropicNonReasoningParameters),
        Model("claude-2.1", k(200), deprecated=True, parameters=AnthropicNonReasoningParameters),
        Model("claude-instant-1.2", k(100), deprecated=True, parameters=AnthropicNonReasoningParameters),
    ],
    "openai": [
        Model("o4-mini", 200000, parameters=OpenAIReasoningParameters),
        Model("o4-mini-high", 200000, deprecated=True),
        Model("gpt-4.1", 1000000, is_translation_default=True),
        Model("gpt-4.1-mini", 1000000, is_default=True),
        Model("gpt-4.1-nano", 1000000),
        Model("o3", 128000, parameters=OpenAIReasoningParameters),
        Model("o3-mini", 128000, parameters=OpenAIReasoningParameters),
        Model("gpt-4o-mini", 128000),
        Model("gpt-4o", 128000),
        Model("chatgpt-4o-latest", 128000),
        Model("o1-preview", 128000, deprecated=True),
        Model("o1-mini", 128000, deprecated=True),
        Model("gpt-4", k(8)),
        Model("gpt-4-turbo", 128000),
        Model("gpt-4-turbo-preview", 128000),
        Model("gpt-4-0125-preview", 128000, deprecated=True),
        Model("gpt-4-1106-preview", 128000, deprecated=True),
        Model("gpt-4-0613", k(8), deprecated=True),
        Model("gpt-3.5-turbo", k(16)),
        Model("gpt-3.5-turbo-1106", k(16)),
        Model("gpt-5", k(8)),
        Model("gpt-5-mini", k(8)),
        Model("gpt-5-nano", k(8)),
        Model("gpt-5-pro", k(8)),
    ],
    "groq": [
        Model("whisper-large-v3", k(8)),
        Model("whisper-large-v3-turbo", k(8)),
        Model("gemma2-9b-it", k(8)),
        Model("gemma-7b-it", k(8), deprecated=True),
        Model("llama3-groq-70b-8192-tool-use-preview", k(8), deprecated=True),
        Model("llama3-groq-8b-8192-tool-use-preview", k(8), deprecated=True),
        Model("llama-3.1-70b-versatile", k(128), is_default=True, is_translation_default=True),
        Model("llama-3.1-8b-instant", k(128)),
        Model("llama-3.2-1b-preview", k(128), deprecated=True),
        Model("llama-3.2-3b-preview", k(128), deprecated=True),
        Model("llama-3.2-11b-vision-preview", k(128), deprecated=True),
        Model("llama-3.2-90b-vision-preview", k(128), deprecated=True),
        Model("llama-guard-3-8b", k(8)),
        Model("llama3-70b-8192", k(8)),
        Model("llama3-8b-8192", k(8)),
        Model("mixtral-8x7b-32768", 32768),
    ],
    "perplexity": [
        Model("sonar", 128000, is_default=True),
        Model("sonar-pro", 200000),
        Model("sonar-reasoning", 128000, is_translation_default=True),
        Model("sonar-reasoning-pro", 128000),
        Model("sonar-deep-research", 128000),
        Model("llama-3.1-sonar-small-128k-online", 127072, deprecated=True),
        Model("llama-3.1-sonar-large-128k-online", 127072, deprecated=True),
        Model("llama-3.1-sonar-huge-128k-online", 127072, deprecated=True),
        Model("llama-3.1-sonar-small-128k-chat", 127072),
        Model("llama-3.1-sonar-large-128k-chat", 127072),
        Model("llama-3.1-8b-instruct", 131072),
        Model("llama-3.1-70b-instruct", 131072),
    ],
    "deepseek": [
        Model("deepseek-chat", 128000, is_default=True),
        Model("deepseek-reasoner", 128000, is_translation_default=True),
    ],
    "google": [
        Model("gemini-2.5-flash", 1048576, is_default=True),
        Model("gemini-2.5-pro", 1048576, is_translation_default=True),
        Model("gemini-2.0-flash", 1048576),
        Model("gemini-1.5-flash", 1048576),
        Model("gemini-1.5-flash-8b", 1048576),
        Model("gemini-1.5-pro", 2097152),
    ],
}


DEFAULT_EMBEDDING_PROVIDER_MODELS = {
    "openai": ["text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002"],
    "google": ["gemini-embedding-001"],
}


LLM_MODEL_PARAMETERS = {}
for _provider, models in DEFAULT_LLM_PROVIDER_MODELS.items():
    for model in models:
        if model.parameters:
            LLM_MODEL_PARAMETERS[model.name] = model.parameters


def get_default_model(provider_type: str) -> Model:
    return next((m for m in DEFAULT_LLM_PROVIDER_MODELS[provider_type] if m.is_default), None)


def get_default_translation_models_by_provider() -> dict:
    """
    Returns a dict mapping provider labels (e.g., "OpenAI") to their default translation model name.
    """
    from apps.service_providers.models import LlmProviderTypes

    defaults = {}
    for provider_type, models in DEFAULT_LLM_PROVIDER_MODELS.items():
        default_model = next((m for m in models if m.is_translation_default), None)
        if default_model:
            provider_label = str(LlmProviderTypes[provider_type].label)
            defaults[provider_label] = default_model.name
    return defaults


@transaction.atomic()
def update_llm_provider_models():
    from apps.service_providers.models import LlmProviderModel

    _update_llm_provider_models(LlmProviderModel)


@transaction.atomic()
def update_embedding_provider_models():
    from apps.service_providers.models import EmbeddingProviderModel

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

    created_models = dict()
    for provider_type, provider_models in DEFAULT_LLM_PROVIDER_MODELS.items():
        for model in provider_models:
            key = (provider_type, model.name)
            if key in existing:
                # update existing global models
                existing_global_model = existing.pop(key)
                if (
                    existing_global_model.max_token_limit != model.token_limit
                    or existing_global_model.deprecated != model.deprecated
                ):
                    existing_global_model.max_token_limit = model.token_limit
                    existing_global_model.deprecated = model.deprecated
                    existing_global_model.save()
            else:
                if not model.deprecated:
                    created_models[(provider_type, model.name)] = LlmProviderModel.objects.create(
                        team=None,
                        type=provider_type,
                        name=model.name,
                        max_token_limit=model.token_limit,
                    )

    # move any that are no longer in the list to be custom models
    for key, provider_model in existing.items():
        related_objects = get_related_objects(provider_model)
        for obj in related_objects:
            custom_model = _get_or_create_custom_model(obj, key, provider_model, existing_custom_by_team)
            field = [f for f in obj._meta.fields if f.related_model == LlmProviderModel][0]
            setattr(obj, field.attname, custom_model.id)
            obj.save(update_fields=[field.name])

        related_pipeline_nodes = get_related_pipelines_queryset(provider_model, "llm_provider_model_id")
        for node in related_pipeline_nodes.select_related("pipeline").all():
            custom_model = _get_or_create_custom_model(node.pipeline, key, provider_model, existing_custom_by_team)
            _update_pipeline_node_param(node.pipeline, node, "llm_provider_model_id", custom_model.id)

        provider_model.delete()

    # replace existing custom models with the new global model and delete the custom models
    for key, model in created_models.items():
        if key in existing_custom_global:
            for custom_model in existing_custom_global[key]:
                related_objects = get_related_objects(custom_model)
                for obj in related_objects:
                    field = [f for f in obj._meta.fields if f.related_model == LlmProviderModel][0]
                    setattr(obj, field.attname, model.id)
                    obj.save(update_fields=[field.name])

                related_pipeline_nodes = get_related_pipelines_queryset(custom_model, "llm_provider_model_id")
                for node in related_pipeline_nodes.select_related("pipeline").all():
                    _update_pipeline_node_param(node.pipeline, node, "llm_provider_model_id", model.id)

                custom_model.delete()


def _get_or_create_custom_model(team_object, key, global_model, existing_custom_by_team):
    """Check the `existing_custom_by_team` mapping for a custom model for the given team and key.
    If one does not exist, create a new custom model and add it to the mapping.
    Return the custom model (existing or new)
    """
    from apps.service_providers.models import LlmProviderModel

    id_key = (team_object.team_id,) + key
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


def _update_pipeline_node_param(pipeline, node, param_name, param_value, commit=True):
    node.params[param_name] = param_value
    if commit:
        node.save()

    data = pipeline.data
    raw_node = [n for n in data["nodes"] if n["id"] == node.flow_id][0]
    raw_node["data"]["params"][param_name] = param_value
    node.pipeline.data = data
    if commit:
        node.pipeline.save()
