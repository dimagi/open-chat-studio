from apps.channels.models import ChannelPlatform, ExperimentChannel


def get_max_char_limit(experiment_version) -> int | None:
    pipeline = experiment_version.pipeline
    if not pipeline:
        return None
    llm_node = pipeline.node_set.filter(type="LLMResponseWithPrompt").first()
    if not llm_node:
        return None
    model_id = llm_node.params.get("llm_provider_model_id")
    if not model_id:
        return None
    from apps.service_providers.models import LlmProviderModel  # noqa: PLC0415

    try:
        max_token_limit = LlmProviderModel.objects.get(id=model_id).max_token_limit
    except LlmProviderModel.DoesNotExist:
        return None
    if not max_token_limit:
        return None
    return max_token_limit * 4


def get_channels_context(experiment) -> tuple[list[ExperimentChannel], dict[ChannelPlatform, bool]]:
    channels = experiment.experimentchannel_set.exclude(platform__in=ChannelPlatform.team_global_platforms()).all()
    used_platforms = {channel.platform_enum for channel in channels}
    available_platforms = ChannelPlatform.for_dropdown(used_platforms, experiment.team)
    return channels, available_platforms
