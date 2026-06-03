"""Experiment-level events walker for the inspect projection (ADR-0025, design D9/D10).

Static and timeout triggers attach to the chatbot, not the pipeline graph, so a node-only walk
would silently omit them — including the inactivity ``TimeoutTrigger`` that is acceptance #4. This
module walks the triggers into an intermediate structure (mirroring ``node_walker``): resource
references are recorded, not yet resolved, so the collector can batch-load them. A ``pipeline_start``
action embeds the referenced pipeline using the same canonical Pipeline walk as the top level; it
does not recurse, since a pipeline carries no triggers of its own.
"""

import dataclasses

from apps.api.v2.inspect.node_walker import PipelineWalk, ResourceRefMap, walk_pipeline
from apps.events.models import EventActionType

# Cadence keys exposed for a ``schedule_trigger`` action (resolved Q3). The cadence lives directly
# on ``EventAction.params``; per-participant ``ScheduledMessage`` rows are runtime state, not config.
_CADENCE_KEYS = ("name", "frequency", "time_period", "repetitions", "prompt_text")


@dataclasses.dataclass
class ActionWalk:
    type: str
    params: dict  # non-resource params to emit alongside ``type`` (e.g. input_type, cadence)
    pipeline: PipelineWalk | None = None  # populated for pipeline_start


@dataclasses.dataclass
class TriggerWalk:
    id: int
    fields: dict  # trigger-level fields (type / delay_seconds / …) emitted alongside id + action
    action: ActionWalk


@dataclasses.dataclass
class EventsWalk:
    static_triggers: list[TriggerWalk]
    timeout_triggers: list[TriggerWalk]
    resource_refs: ResourceRefMap


def _merge_refs(into: ResourceRefMap, more: ResourceRefMap) -> None:
    for kind, ids in more.items():
        into.setdefault(kind, set()).update(ids)


def walk_action(action, team, resource_refs: ResourceRefMap) -> ActionWalk:
    """Walk a single :class:`~apps.events.models.EventAction` into an ``ActionWalk``.

    ``pipeline_start`` embeds the referenced pipeline (team-scoped) as a full ``PipelineWalk`` and
    folds its resource references into ``resource_refs``. ``schedule_trigger`` surfaces the cadence.
    Other action types pass their params through verbatim (they hold no resource references).
    """
    action_type = action.action_type
    params = dict(action.params or {})

    if action_type == EventActionType.PIPELINE_START:
        from apps.pipelines.models import Pipeline  # noqa: PLC0415 - avoid import cycle at module load

        pipeline_id = params.pop("pipeline_id", None)
        pipeline_walk = None
        if pipeline_id is not None:
            pipeline = Pipeline.objects.filter(team=team, id=pipeline_id).first()
            if pipeline is not None:
                pipeline_walk = walk_pipeline(pipeline)
                _merge_refs(resource_refs, pipeline_walk.resource_refs)
        return ActionWalk(type=action_type, params=params, pipeline=pipeline_walk)

    if action_type == EventActionType.SCHEDULETRIGGER:
        cadence = {key: params.get(key) for key in _CADENCE_KEYS}
        return ActionWalk(type=action_type, params={"scheduled_message": cadence})

    return ActionWalk(type=action_type, params=params)


def walk_events(experiment) -> EventsWalk:
    """Walk a chatbot's static and timeout triggers (excluding archived). Disabled triggers are
    included but flagged via ``is_active`` so a verifier can assert a trigger is *not* armed."""
    team = experiment.team
    resource_refs: ResourceRefMap = {}

    static = []
    for trigger in experiment.static_triggers.filter(is_archived=False).select_related("action"):
        static.append(
            TriggerWalk(
                id=trigger.id,
                fields={"type": trigger.type, "is_active": trigger.is_active},
                action=walk_action(trigger.action, team, resource_refs),
            )
        )

    timeout = []
    for trigger in experiment.timeout_triggers.filter(is_archived=False).select_related("action"):
        timeout.append(
            TriggerWalk(
                id=trigger.id,
                fields={
                    "delay_seconds": trigger.delay,
                    "total_num_triggers": trigger.total_num_triggers,
                    "trigger_from_first_message": trigger.trigger_from_first_message,
                    "is_active": trigger.is_active,
                },
                action=walk_action(trigger.action, team, resource_refs),
            )
        )

    return EventsWalk(static_triggers=static, timeout_triggers=timeout, resource_refs=resource_refs)
