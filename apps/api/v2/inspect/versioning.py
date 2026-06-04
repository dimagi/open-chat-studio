"""Inspect target resolution + prefetch (relocated from ``builder.py``).

Pure target-preparation helpers: resolve ``?version=`` to a concrete Experiment, then re-fetch it
with the select_related / prefetch_related the inspect serializers and ``ResourceFetcher`` need.
Prefetch is applied to the **resolved** target (not the family) so versioned reads keep the
N+1-free query profile.
"""

from django.db.models import Prefetch

from apps.chatbots.version_resolver import (
    NoPublishedVersion,
    VersionNotFound,
    VersionSelectionRule,
    resolve_chatbot_version,
)
from apps.events.models import StaticTrigger, TimeoutTrigger
from apps.experiments.models import Experiment


class InspectVersionError(ValueError):
    """The requested ``?version=`` could not be resolved (unknown number / no published version)."""


def resolve_inspect_version(family, version_param: str | None):
    """Resolve the ``?version=`` query parameter to a target Experiment version.

    - ``None`` (omitted) -> the working (draft) family head.
    - ``"default"`` -> the default published version.
    - an integer string -> that specific version number.
    """
    if version_param is None:
        return family
    try:
        if version_param == "default":
            return resolve_chatbot_version(family, VersionSelectionRule.LATEST_PUBLISHED)
        return resolve_chatbot_version(family, VersionSelectionRule.SPECIFIC, version_number=int(version_param))
    except (ValueError, NoPublishedVersion, VersionNotFound) as err:
        raise InspectVersionError(str(err)) from err


def prefetch_inspect_target(target: Experiment) -> Experiment:
    """Re-fetch the resolved target with the FK/relation prefetches the inspect render needs.

    Triggers are prefetched unfiltered (archived exclusion happens in the serializers); the inner
    ``select_related("action")`` lets the event/action serializers and the fetcher pre-pass read
    each action without a per-trigger query."""
    return (
        Experiment.objects.select_related(
            "team",
            "consent_form",
            "pre_survey",
            "post_survey",
            "trace_provider",
            "voice_provider",
            "synthetic_voice",
            "synthetic_voice__voice_provider",
            "pipeline",
        )
        .prefetch_related(
            "pipeline__node_set",
            Prefetch("static_triggers", queryset=StaticTrigger.objects.select_related("action")),
            Prefetch("timeout_triggers", queryset=TimeoutTrigger.objects.select_related("action")),
        )
        .get(pk=target.pk)
    )
