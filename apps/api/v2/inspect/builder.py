"""Top-level orchestration for the chatbot inspect projection (ADR-0024/0025).

Assembles the denormalized, secrets-excluded payload: the chatbot's own fields and settings, its
embedded resources, channels, the pipeline (graph digest + per-node detail with resources inlined),
and the experiment-level events block. The response root *is* the chatbot — there is no wrapper key.
"""

from apps.api.v2.inspect.collector import InspectCollector
from apps.api.v2.inspect.events import walk_events
from apps.api.v2.inspect.node_walker import PipelineWalk, walk_pipeline
from apps.api.v2.inspect.serializers import (
    ChannelSerializer,
    ConsentFormSerializer,
    ProviderSerializer,
    SurveySerializer,
    serialize_synthetic_voice,
)
from apps.channels.models import ExperimentChannel
from apps.chatbots.version_resolver import (
    NoPublishedVersion,
    VersionNotFound,
    VersionSelectionRule,
    resolve_chatbot_version,
)

# Non-secret Experiment fields surfaced under ``settings``.
_SETTINGS_FIELDS = [
    "seed_message",
    "conversational_consent_enabled",
    "voice_response_behaviour",
    "echo_transcript",
    "use_processor_bot_voice",
    "debug_mode_enabled",
    "file_uploads_enabled",
    "participant_allowlist",
]


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


def build_inspect_payload(experiment) -> dict:
    """Build the inspect projection for an already-resolved chatbot version."""
    team = experiment.team

    pipeline_walk = walk_pipeline(experiment.pipeline) if experiment.pipeline_id else None
    events_walk = walk_events(experiment)

    collector = InspectCollector(team).load(
        pipeline_walk.resource_refs if pipeline_walk else {},
        events_walk.resource_refs,
    )

    return {
        "id": str(experiment.public_id),
        "name": experiment.name,
        "description": experiment.description,
        "version_number": experiment.version_number,
        "is_unreleased": experiment.is_working_version,
        "is_published_version": experiment.is_default_version,
        "version_description": experiment.version_description,
        "team_slug": team.slug,
        "settings": {field: getattr(experiment, field) for field in _SETTINGS_FIELDS},
        "consent_form": _serialize_or_none(ConsentFormSerializer, experiment.consent_form),
        "pre_survey": _serialize_or_none(SurveySerializer, experiment.pre_survey),
        "post_survey": _serialize_or_none(SurveySerializer, experiment.post_survey),
        "voice": serialize_synthetic_voice(experiment.voice_provider, experiment.synthetic_voice),
        "trace_provider": _serialize_or_none(ProviderSerializer, experiment.trace_provider),
        "channels": _serialize_channels(experiment),
        "pipeline": _render_pipeline(pipeline_walk, collector),
        "events": _render_events(events_walk, collector),
    }


def _serialize_or_none(serializer_cls, instance):
    return serializer_cls(instance).data if instance is not None else None


def _serialize_channels(experiment) -> list[dict]:
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
    return ChannelSerializer(channels, many=True).data


def _render_pipeline(walk: PipelineWalk | None, collector: InspectCollector) -> dict | None:
    if walk is None:
        return None
    return {
        "id": walk.id,
        "name": walk.name,
        "version_number": walk.version_number,
        "graph": walk.graph,
        "nodes": [
            {
                "flow_id": node.flow_id,
                "type": node.type,
                "label": node.label,
                "params": node.params,
                **collector.inline_refs(node.refs),
            }
            for node in walk.nodes
        ],
    }


def _render_events(events_walk, collector: InspectCollector) -> dict:
    def render_action(action) -> dict:
        rendered = {"type": action.type, **action.params}
        if action.pipeline is not None:
            rendered["pipeline"] = _render_pipeline(action.pipeline, collector)
        return rendered

    def render_trigger(trigger) -> dict:
        return {"id": trigger.id, **trigger.fields, "action": render_action(trigger.action)}

    return {
        "static_triggers": [render_trigger(t) for t in events_walk.static_triggers],
        "timeout_triggers": [render_trigger(t) for t in events_walk.timeout_triggers],
    }
