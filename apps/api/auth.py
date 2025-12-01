from apps.api.exceptions import EmbeddedWidgetAuthError
from apps.channels.utils import (
    avalidate_embed_key_for_experiment,
    extract_domain_from_headers,
    validate_embed_key_for_experiment,
)


def handle_embedded_widget_auth(request, experiment_id=None, session=None):
    """
    Validate embedded widget authentication.
    Returns:
        ExperimentChannel if authentication succeeds, None if no embed key present.
    """
    embed_key = request.headers.get("X-Embed-Key")
    if not embed_key:
        # follow legacy workflow
        return None

    origin_domain = extract_domain_from_headers(request)
    if not origin_domain:
        raise EmbeddedWidgetAuthError("Origin or Referer header required for embedded widgets")

    if experiment_id:
        target_experiment_id = experiment_id
    elif session:
        target_experiment_id = session.experiment.public_id
    else:
        raise EmbeddedWidgetAuthError("Either experiment_id or session must be provided")

    experiment_channel = validate_embed_key_for_experiment(
        token=embed_key, origin_domain=origin_domain, experiment_id=target_experiment_id
    )
    if not experiment_channel:
        raise EmbeddedWidgetAuthError("Invalid embed key or domain not allowed")

    return experiment_channel


async def ahandle_embedded_widget_auth(request, experiment_id=None, session=None):
    """
    Validate embedded widget authentication.
    Returns:
        ExperimentChannel if authentication succeeds, None if no embed key present.
    """
    embed_key = request.headers.get("X-Embed-Key")
    if not embed_key:
        # follow legacy workflow
        return None

    origin_domain = extract_domain_from_headers(request)
    if not origin_domain:
        raise EmbeddedWidgetAuthError("Origin or Referer header required for embedded widgets")

    if experiment_id:
        target_experiment_id = experiment_id
    elif session:
        target_experiment_id = session.experiment.public_id
    else:
        raise EmbeddedWidgetAuthError("Either experiment_id or session must be provided")

    experiment_channel = await avalidate_embed_key_for_experiment(
        token=embed_key, origin_domain=origin_domain, experiment_id=target_experiment_id
    )
    if not experiment_channel:
        raise EmbeddedWidgetAuthError("Invalid embed key or domain not allowed")

    return experiment_channel
