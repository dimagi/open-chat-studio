"""Inspect target resolution + prefetch (relocated from ``builder.py``).

Pure target-preparation helpers: resolve ``?version=`` to a concrete Experiment, then re-fetch it
with the select_related / prefetch_related the inspect serializers and ``ResourceFetcher`` need.
Prefetch is applied to the **resolved** target (not the family) so versioned reads keep the
N+1-free query profile.
"""

from django.db.models import Prefetch

from apps.events.models import StaticTrigger, TimeoutTrigger
from apps.experiments.models import Experiment


class InspectVersionError(ValueError):
    """The requested ``?version=`` could not be resolved (unknown number / no published version)."""


def resolve_inspect_version(public_id: str, version_param: str | None, *, team):
    """Resolve the ``?version=`` query parameter to a target Experiment version.

    - ``None`` (omitted) -> the working (draft) family head.
    - ``"default"`` -> the default published version.
    - an integer string -> that specific version number.

    Scoped to ``team`` so a ``public_id`` from another team resolves to nothing (404 at the view).
    Each path resolves in a single query: the family head is read by ``public_id`` directly, and
    versioned reads join through ``working_version__public_id`` rather than fetching the family first.
    """
    if version_param is None:
        target = Experiment.objects.filter(public_id=public_id, team=team).first()
    elif version_param == "default":
        target = Experiment.objects.filter(
            working_version__public_id=public_id, team=team, is_default_version=True
        ).first()
    else:
        try:
            version_number = int(version_param)
        except ValueError as err:
            raise InspectVersionError(f"Invalid version: {version_param!r}") from err
        target = Experiment.objects.filter(
            working_version__public_id=public_id, team=team, version_number=version_number
        ).first()

    if target:
        return target
    raise InspectVersionError("Version not found")


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
