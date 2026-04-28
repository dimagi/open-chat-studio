from django.core.cache import cache

from apps.channels.models import ChannelPlatform, ExperimentChannel

# LangChain's count_tokens_approximately uses len(text) // 4, so multiply by 4
# to convert a token limit back to an approximate character limit that is
# consistent with the backend enforcement in _validate_user_message_size.
_CHARS_PER_TOKEN = 4


def get_max_char_limit(experiment_version) -> int | None:
    pipeline = experiment_version.pipeline
    if not pipeline:
        return None

    # Key encodes pipeline.updated_at so any pipeline save naturally busts it.
    # No explicit TTL needed — cache backend eviction handles orphaned keys.
    cache_key = f"max_char_limit:{experiment_version.id}:{pipeline.updated_at.timestamp()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = _compute_max_char_limit(pipeline)
    if result is not None:
        cache.set(cache_key, result)
    return result


def _compute_max_char_limit(pipeline) -> int | None:
    from apps.pipelines.nodes.nodes import LLMResponseWithPrompt  # noqa: PLC0415

    llm_nodes = pipeline.node_set.filter(type=LLMResponseWithPrompt.__name__)
    if not llm_nodes.exists():
        return None

    from apps.service_providers.models import LlmProviderModel  # noqa: PLC0415

    model_ids = [n.params.get("llm_provider_model_id") for n in llm_nodes if n.params.get("llm_provider_model_id")]
    if not model_ids:
        return None

    limits = list(
        LlmProviderModel.objects.filter(id__in=model_ids)
        .exclude(max_token_limit__isnull=True)
        .exclude(max_token_limit=0)
        .values_list("max_token_limit", flat=True)
    )
    if not limits:
        return None
    return min(limits) * _CHARS_PER_TOKEN


def get_channels_context(experiment) -> tuple[list[ExperimentChannel], dict[ChannelPlatform, bool]]:
    channels = experiment.experimentchannel_set.exclude(platform__in=ChannelPlatform.team_global_platforms()).all()
    used_platforms = {channel.platform_enum for channel in channels}
    available_platforms = ChannelPlatform.for_dropdown(used_platforms, experiment.team)
    return channels, available_platforms
