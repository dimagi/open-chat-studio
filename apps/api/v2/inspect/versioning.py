"""Turn the ``?version=`` query parameter into the chatbot version to inspect.

The chosen version comes back already loaded with the related objects the inspect serializers need,
including each pipeline node's resource FK/M2M relations (via ``inspect_node_queryset``). Picking the
version and preloading its relations happen in one query — each ``?version=`` mode filters the
preloaded queryset directly — so no follow-up round trip is needed.
"""

from django.db.models import Prefetch

from apps.api.v2.inspect.nodes import inspect_node_queryset
from apps.events.models import StaticTrigger, TimeoutTrigger
from apps.experiments.models import Experiment


class InspectVersionError(ValueError):
    """Raised when ``?version=`` doesn't match a version (unknown number, or no published version)."""


def _inspect_target_queryset():
    return Experiment.objects.select_related(
        "team",
        "consent_form",
        "trace_provider",
        "voice_provider",
        "synthetic_voice",
        "synthetic_voice__voice_provider",
        "pipeline",
    ).prefetch_related(
        Prefetch("pipeline__node_set", queryset=inspect_node_queryset()),
        Prefetch("static_triggers", queryset=StaticTrigger.objects.select_related("action")),
        Prefetch("timeout_triggers", queryset=TimeoutTrigger.objects.select_related("action")),
    )


def resolve_inspect_version(public_id: str, version_param: str | None, *, team) -> Experiment:
    """Resolve ``?version=`` to the matching chatbot version, fully preloaded.

    - omitted (``None``) -> the working (draft) version.
    - ``"default"`` -> the default published version.
    - an integer string -> that specific version number.

    Scoped to ``team``, so a ``public_id`` from another team matches nothing (the view returns a 404).
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
