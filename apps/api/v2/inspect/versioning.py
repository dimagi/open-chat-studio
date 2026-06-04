"""Inspect target resolution (relocated from ``builder.py``).

Resolves ``?version=`` to a concrete Experiment, already loaded with the select_related /
prefetch_related the inspect serializers and ``ResourceFetcher`` need. Resolution and prefetch
happen in a single pass: each ``?version=`` mode filters the prefetched queryset directly, so a
versioned read keeps the N+1-free query profile without a separate re-fetch round trip.
"""

from django.db.models import Prefetch

from apps.events.models import StaticTrigger, TimeoutTrigger
from apps.experiments.models import Experiment


class InspectVersionError(ValueError):
    """The requested ``?version=`` could not be resolved (unknown number / no published version)."""


def _inspect_target_queryset():
    """Base queryset carrying the FK/relation prefetches the inspect render needs.

    Triggers are prefetched unfiltered (archived exclusion happens in the serializers); the inner
    ``select_related("action")`` lets the event/action serializers and the fetcher pre-pass read
    each action without a per-trigger query."""
    return Experiment.objects.select_related(
        "team",
        "consent_form",
        "pre_survey",
        "post_survey",
        "trace_provider",
        "voice_provider",
        "synthetic_voice",
        "synthetic_voice__voice_provider",
        "pipeline",
    ).prefetch_related(
        "pipeline__node_set",
        Prefetch("static_triggers", queryset=StaticTrigger.objects.select_related("action")),
        Prefetch("timeout_triggers", queryset=TimeoutTrigger.objects.select_related("action")),
    )


def resolve_inspect_version(public_id: str, version_param: str | None, *, team) -> Experiment:
    """Resolve the ``?version=`` query parameter to a fully-prefetched target Experiment version.

    - ``None`` (omitted) -> the working (draft) family head.
    - ``"default"`` -> the default published version.
    - an integer string -> that specific version number.

    Scoped to ``team`` so a ``public_id`` from another team resolves to nothing (404 at the view).
    Each path resolves and prefetches in a single pass: the family head is read by ``public_id``
    directly, and versioned reads join through ``working_version__public_id`` rather than fetching
    the family first. The returned target is loaded with the FK/relation prefetches the inspect
    serializers and ``ResourceFetcher`` need.
    """
    base = _inspect_target_queryset()
    if version_param is None:
        target = base.filter(public_id=public_id, team=team).first()
    elif version_param == "default":
        target = base.filter(working_version__public_id=public_id, team=team, is_default_version=True).first()
    else:
        try:
            version_number = int(version_param)
        except ValueError as err:
            raise InspectVersionError(f"Invalid version: {version_param!r}") from err
        target = base.filter(working_version__public_id=public_id, team=team, version_number=version_number).first()

    if target:
        return target
    raise InspectVersionError("Version not found")
