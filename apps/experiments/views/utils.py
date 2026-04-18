from apps.channels.models import ChannelPlatform, ExperimentChannel


def get_max_char_limit(experiment_version) -> int | None:
    from apps.pipelines.nodes.nodes import LLMResponseWithPrompt  # noqa: PLC0415

    pipeline = experiment_version.pipeline
    if not pipeline:
        return None
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
    return min(limits) * 4


def get_channels_context(experiment) -> tuple[list[ExperimentChannel], dict[ChannelPlatform, bool]]:
    channels = experiment.experimentchannel_set.exclude(platform__in=ChannelPlatform.team_global_platforms()).all()
    used_platforms = {channel.platform_enum for channel in channels}
    available_platforms = ChannelPlatform.for_dropdown(used_platforms, experiment.team)
    return channels, available_platforms
