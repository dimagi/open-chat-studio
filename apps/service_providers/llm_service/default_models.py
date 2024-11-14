import dataclasses
from collections import defaultdict

from django.db import transaction

from apps.utils.deletion import get_related_objects, get_related_pipelines_queryset


@dataclasses.dataclass
class Model:
    name: str
    token_limit: int


def k(n: int) -> int:
    return n * 1024


DEFAULT_LLM_PROVIDER_MODELS = {
    "azure": [
        Model("gpt-4o-mini", 128000),
        Model("gpt-4o", 128000),
        Model("gpt-4", k(8)),
        Model("gpt-4-32k", 32768),
        Model("gpt-35-turbo", 16385),
        Model("gpt-35-turbo-16k", 16384),
    ],
    "anthropic": [
        Model("claude-3-5-sonnet-latest", k(200)),
        Model("claude-3-5-haiku-latest", k(200)),
        Model("claude-3-opus-latest", k(200)),
        Model("claude-2.0", k(100)),
        Model("claude-2.1", k(200)),
        Model("claude-instant-1.2", k(100)),
    ],
    "openai": [
        Model("gpt-4o-mini", 128000),
        Model("gpt-4o", 128000),
        Model("chatgpt-4o-latest", 128000),
        Model("o1-preview", 128000),
        Model("o1-mini", 128000),
        Model("gpt-4", k(8)),
        Model("gpt-4-turbo", 128000),
        Model("gpt-4-turbo-preview", 128000),
        Model("gpt-4-0125-preview", 128000),
        Model("gpt-4-1106-preview", 128000),
        Model("gpt-4-0613", k(8)),
        Model("gpt-3.5-turbo", k(16)),
        Model("gpt-3.5-turbo-1106", k(16)),
    ],
    "groq": [
        Model("gemma2-9b-it", k(8)),
        Model("gemma-7b-it", k(8)),
        Model("llama3-groq-70b-8192-tool-use-preview", k(8)),
        Model("llama3-groq-8b-8192-tool-use-preview", k(8)),
        Model("llama-3.1-70b-versatile", k(128)),
        Model("llama-3.1-8b-instant", k(128)),
        Model("llama-3.2-1b-preview", k(128)),
        Model("llama-3.2-3b-preview", k(128)),
        Model("llama-3.2-11b-vision-preview", k(128)),
        Model("llama-3.2-90b-vision-preview", k(128)),
        Model("llama-guard-3-8b", k(8)),
        Model("llama3-70b-8192", k(8)),
        Model("llama3-8b-8192", k(8)),
        Model("mixtral-8x7b-32768", 32768),
    ],
    "perplexity": [
        Model("llama-3.1-sonar-small-128k-online", 127072),
        Model("llama-3.1-sonar-large-128k-online", 127072),
        Model("llama-3.1-sonar-huge-128k-online", 127072),
        Model("llama-3.1-sonar-small-128k-chat", 127072),
        Model("llama-3.1-sonar-large-128k-chat", 127072),
        Model("llama-3.1-8b-instruct", 131072),
        Model("llama-3.1-70b-instruct", 131072),
    ],
}


@transaction.atomic()
def update_llm_provider_models():
    """
    This method updates the LlmProviderModel objects in the database to match the DEFAULT_LLM_PROVIDER_MODELS.
    If a model exists in the database that is not in DEFAULT_LLM_PROVIDER_MODELS, it is deleted.
    If a model exists in DEFAULT_LLM_PROVIDER_MODELS that is not in the database, it is created.

    Any references to models that are going to be deleted are updated to reference a custom model (which is created
    if it does not already exist).
    """

    from apps.service_providers.models import LlmProviderModel

    existing = {(m.type, m.name): m for m in LlmProviderModel.objects.filter(team=None)}
    existing_custom_by_team = {
        (m.team_id, m.type, m.name): m for m in LlmProviderModel.objects.filter(team__isnull=False)
    }
    existing_custom_global = defaultdict(list)
    for m in existing_custom_by_team.values():
        existing_custom_global[(m.type, m.name)].append(m)

    for provider_type, provider_models in DEFAULT_LLM_PROVIDER_MODELS.items():
        for model in provider_models:
            key = (provider_type, model.name)
            if key in existing:
                # update existing global models
                existing_global_model = existing.pop(key)
                if existing_global_model.max_token_limit != model.token_limit:
                    existing_global_model.max_token_limit = model.token_limit
                    existing_global_model.save()
            else:
                LlmProviderModel.objects.create(
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
            setattr(obj, field.name, custom_model)
            obj.save(update_fields=[field.name])

        related_pipeline_nodes = get_related_pipelines_queryset(provider_model, "llm_provider_model_id")
        for node in related_pipeline_nodes.select_related("pipeline").all():
            custom_model = _get_or_create_custom_model(node.pipeline, key, provider_model, existing_custom_by_team)
            _update_pipeline_node_param(node.pipeline, node, "llm_provider_model_id", custom_model.id)

        provider_model.delete()


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
