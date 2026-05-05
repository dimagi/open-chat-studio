from django.core.cache import cache

from apps.service_providers.models import LlmProviderModel


def compute_max_char_limit(pipeline) -> int | None:
    """Return the maximum input character limit for the given pipeline, or None if unconstrained.

    Cached by pipeline.id + pipeline.updated_at — any pipeline save naturally busts the cache.
    Reads the smallest model max_token_limit across all LLM nodes and converts to characters
    using LangChain's approximation ratio.
    """
    cache_key = f"max_char_limit:{pipeline.id}:{pipeline.updated_at.timestamp()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = _compute_max_char_limit(pipeline)
    if result is not None:
        cache.set(cache_key, result)
    return result


def _compute_max_char_limit(pipeline) -> int | None:
    from langchain_core.messages.utils import count_tokens_approximately  # noqa: PLC0415

    from apps.pipelines.nodes.nodes import (  # noqa: PLC0415 - lazy: nodes.py loads heavy langchain/pydantic deps
        LLMResponseWithPrompt,
    )

    llm_nodes = list(pipeline.node_set.filter(type=LLMResponseWithPrompt.__name__))
    if not llm_nodes:
        return None

    limits = []
    model_ids_needing_lookup = []

    for node in llm_nodes:
        if node.params.get("llm_provider_model_id"):
            model_ids_needing_lookup.append(node.params["llm_provider_model_id"])

    if model_ids_needing_lookup:
        model_limits = list(
            LlmProviderModel.objects.filter(id__in=model_ids_needing_lookup)
            .exclude(max_token_limit__isnull=True)
            .exclude(max_token_limit=0)
            .values_list("max_token_limit", flat=True)
        )
        limits.extend(model_limits)

    if not limits:
        return None

    token_limit = min(limits)
    # Read chars_per_token from LangChain's function default so the frontend counter
    # always uses the same ratio as MessageSizeValidationMiddleware.
    chars_per_token = (count_tokens_approximately.__kwdefaults__ or {}).get("chars_per_token", 4.0)
    return int(token_limit * chars_per_token)
