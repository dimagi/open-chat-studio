"""Synchronisation of event triggers between experiments in a version family.

This module must not import ``apps.events.models`` at module level: it is imported
by ``apps.experiments.models``, which ``apps.events.models`` itself imports.
"""

from typing import TYPE_CHECKING

from django.db import transaction

if TYPE_CHECKING:
    from apps.experiments.models import Experiment

TRIGGER_ACCESSORS = ("static_triggers", "timeout_triggers")


@transaction.atomic()
def sync_triggers(source: "Experiment", target: "Experiment", is_copy: bool = False) -> None:
    """Make ``target``'s static and timeout triggers mirror ``source``'s.

    Every trigger on ``source`` is copied onto ``target`` together with its
    ``EventAction``; triggers already on ``target`` have no counterpart on ``source``
    and are archived. Publish uses this working -> version (``target`` is a freshly
    created version with no triggers); revert (#3398) uses it version -> working.
    """
    for accessor in TRIGGER_ACCESSORS:
        stale_triggers = list(getattr(target, accessor).all())
        for trigger in getattr(source, accessor).all():
            trigger.create_new_version(new_experiment=target, is_copy=is_copy)
        for trigger in stale_triggers:
            trigger.archive()
