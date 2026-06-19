"""Synchronisation of event triggers between experiments in a version family.

This module must not import ``apps.events.models`` at module level: it is imported
by ``apps.experiments.models``, which ``apps.events.models`` itself imports.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from django.apps import apps
from django.db import transaction

if TYPE_CHECKING:
    from apps.events.models import EventAction
    from apps.experiments.models import Experiment

TRIGGER_ACCESSORS = ("static_triggers", "timeout_triggers")


class TriggerSyncMode(StrEnum):
    """Direction of a :func:`sync_triggers` call, which determines how versioned
    references in ``EventAction.params`` are rewritten."""

    PUBLISH = "publish"
    """working -> version: pin params that reference versioned records to a version."""

    REVERT = "revert"
    """version -> working: map versioned params back to their working records."""

    COPY = "copy"
    """duplicate a working experiment: keep params verbatim (still working refs)."""


@dataclass(frozen=True)
class EventActionParamSpec:
    """Declares an ``EventAction.params`` entry that references a versioned record.

    Mirrors ``apps.pipelines.versioning.VersionedParamSpec`` for pipeline-node params,
    but ``EventAction`` has no FK mirror column, so the referenced id is read straight
    from ``params``. Publish pins the working record to a version (creating one only
    when the record changed); revert maps a versioned record back to its working id.
    """

    param_name: str
    model_label: str  # "app_label.ModelName"; resolved lazily to avoid circular imports

    @property
    def model_cls(self):
        return apps.get_model(self.model_label)

    def pin_to_version(self, params: dict) -> None:
        """Rewrite ``params[param_name]`` from a working record to a version of it."""
        instance = self._get_instance(params)
        if instance is None or instance.is_a_version:
            return
        if not instance.has_versions or instance.compare_with_latest():
            params[self.param_name] = instance.create_new_version().id
        else:
            params[self.param_name] = instance.latest_version.id

    def revert_to_working(self, params: dict) -> None:
        """Rewrite ``params[param_name]`` from a versioned record back to its working id.

        Records already pointing at a working version (legacy unpinned data) resolve to
        themselves. A dangling reference (record deleted) is cleared to ``None``.
        """
        if not params.get(self.param_name):
            return
        instance = self._get_instance(params)
        params[self.param_name] = instance.get_working_version_id() if instance else None

    def _get_instance(self, params: dict):
        instance_id = params.get(self.param_name)
        if not instance_id:
            return None
        return self.model_cls.objects.filter(id=instance_id).first()


# Keyed by ``EventActionType`` value (string literal to avoid importing events.models here).
_EVENT_ACTION_PARAM_SPECS: dict[str, tuple[EventActionParamSpec, ...]] = {
    "pipeline_start": (EventActionParamSpec(param_name="pipeline_id", model_label="pipelines.Pipeline"),),
}


def get_event_action_param_specs(action_type: str) -> tuple[EventActionParamSpec, ...]:
    return _EVENT_ACTION_PARAM_SPECS.get(action_type, ())


@transaction.atomic()
def sync_triggers(source: "Experiment", target: "Experiment", mode: TriggerSyncMode = TriggerSyncMode.PUBLISH) -> None:
    """Make ``target``'s static and timeout triggers mirror ``source``'s.

    Every trigger on ``source`` is copied onto ``target`` together with its
    ``EventAction``; triggers already on ``target`` with no counterpart on ``source``
    are archived. ``mode`` selects the direction: publish (working -> version) pins
    versioned references, revert (version -> working) maps them back, and copy keeps
    them verbatim.
    """
    # Publish records the new version's link back to the working trigger; revert and copy
    # produce standalone working triggers (no ``working_version`` link).
    is_copy = mode is not TriggerSyncMode.PUBLISH
    for accessor in TRIGGER_ACCESSORS:
        stale_triggers = list(getattr(target, accessor).all())
        for trigger in getattr(source, accessor).all():
            new_trigger = trigger.create_new_version(new_experiment=target, is_copy=is_copy)
            if is_copy and new_trigger.working_version_id is not None:
                # Reverting clones from a version row, which carries a ``working_version`` link;
                # clear it so the restored trigger is a working trigger in its own right.
                new_trigger.working_version_id = None
                new_trigger.save(update_fields=["working_version"])
            _remap_action_params(new_trigger.action, mode)
        for trigger in stale_triggers:
            trigger.archive()


def _remap_action_params(action: "EventAction", mode: TriggerSyncMode) -> None:
    specs = get_event_action_param_specs(action.action_type)
    if not specs:
        return
    for spec in specs:
        if mode is TriggerSyncMode.PUBLISH:
            spec.pin_to_version(action.params)
        elif mode is TriggerSyncMode.REVERT:
            spec.revert_to_working(action.params)
    action.save(update_fields=["params"])
