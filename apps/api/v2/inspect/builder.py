"""Top-level orchestration for the chatbot inspect projection (ADR-0024/0025).

Walks the pipeline and events, batch-loads referenced resources, and assembles the
``InspectContext`` instance tree consumed by
:class:`apps.api.v2.inspect.serializers.ChatbotInspectSerializer`. No serialization happens here —
the response root *is* the chatbot, rendered entirely by the response serializers.
"""

import dataclasses

from apps.api.v2.inspect.collector import InspectCollector
from apps.api.v2.inspect.events import ActionWalk, EventsWalk, TriggerWalk, walk_events
from apps.api.v2.inspect.node_walker import NodeWalkResult, PipelineWalk, walk_pipeline
from apps.api.v2.inspect.serializers import VoicePair
from apps.channels.models import ExperimentChannel
from apps.chatbots.version_resolver import (
    NoPublishedVersion,
    VersionNotFound,
    VersionSelectionRule,
    resolve_chatbot_version,
)
from apps.experiments.models import Experiment


class InspectVersionError(ValueError):
    """The requested ``?version=`` could not be resolved (unknown number / no published version)."""


@dataclasses.dataclass
class InspectContext:
    """Instance tree consumed by ``ChatbotInspectSerializer`` — loaded model instances, resolved
    pairs, and walk products; no serialized dicts."""

    experiment: Experiment
    voice: VoicePair | None
    channels: list[ExperimentChannel]
    pipeline: dict | None  # pre-structured walk product; leaf values are model instances / pairs
    events: dict  # pre-structured walk product; leaf values are model instances / pairs


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


def build_inspect_context(experiment: Experiment) -> InspectContext:
    """Build the inspect instance tree for an already-resolved chatbot version."""
    team = experiment.team

    pipeline_walk = walk_pipeline(experiment.pipeline) if experiment.pipeline_id else None
    events_walk = walk_events(experiment)

    collector = InspectCollector(team).load(
        pipeline_walk.resource_refs if pipeline_walk else {},
        events_walk.resource_refs,
    )

    return InspectContext(
        experiment=experiment,
        voice=VoicePair.from_parts(experiment.voice_provider, experiment.synthetic_voice),
        channels=_collect_channels(experiment),
        pipeline=_resolve_pipeline(pipeline_walk, collector),
        events=_resolve_events(events_walk, collector),
    )


def _collect_channels(experiment: Experiment) -> list[ExperimentChannel]:
    # Channels are only ever linked to the working version, so resolve the family head regardless
    # of which version is being inspected.
    channels = list(
        ExperimentChannel.objects.filter(experiment_id=experiment.get_working_version_id()).select_related(
            "messaging_provider"
        )
    )
    # The web and API channels are team-global (linked to no experiment); every chatbot is
    # reachable through them.
    channels.append(ExperimentChannel.objects.get_team_web_channel(experiment.team))
    channels.append(ExperimentChannel.objects.get_team_api_channel(experiment.team))
    return channels


def _node_render_order(node) -> int:
    """Pin the start node first and the end node last; everything else keeps creation order."""
    return {"StartNode": 0, "EndNode": 2}.get(node.type, 1)


def _resolve_pipeline(walk: PipelineWalk | None, collector: InspectCollector) -> dict | None:
    if walk is None:
        return None
    return {
        "id": walk.id,
        "name": walk.name,
        "version_number": walk.version_number,
        "graph": walk.graph,
        "nodes": [_resolve_node(node, collector) for node in sorted(walk.nodes, key=_node_render_order)],
    }


def _resolve_node(node: NodeWalkResult, collector: InspectCollector) -> dict:
    # A reference that is unset or resolved to absent (cross-team / deleted id) is omitted —
    # key present <=> resource embedded. Empty lists are kept.
    refs = {key: value for key, value in collector.resolve_refs(node.refs).items() if value is not None}
    return {"flow_id": node.flow_id, "type": node.type, "label": node.label, "params": node.params, **refs}


def _resolve_events(events_walk: EventsWalk, collector: InspectCollector) -> dict:
    return {
        "static_triggers": [_resolve_trigger(t, collector) for t in events_walk.static_triggers],
        "timeout_triggers": [_resolve_trigger(t, collector) for t in events_walk.timeout_triggers],
    }


def _resolve_trigger(trigger: TriggerWalk, collector: InspectCollector) -> dict:
    return {"id": trigger.id, **trigger.fields, "action": _resolve_action(trigger.action, collector)}


def _resolve_action(action: ActionWalk, collector: InspectCollector) -> dict:
    resolved: dict = {"type": action.type, "params": action.params}
    if action.pipeline is not None:
        resolved["pipeline"] = _resolve_pipeline(action.pipeline, collector)
    return resolved
