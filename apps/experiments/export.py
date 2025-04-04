import csv
import io

from apps.annotations.models import Tag, UserComment
from apps.experiments.filters import apply_dynamic_filters
from apps.experiments.models import ExperimentSession


def _format_tags(tags: list[Tag]) -> str:
    """Returns `tags` parsed into a single string in the format 'tag1, tag2, tag3'"""
    return ", ".join([t.name for t in tags])


def _format_comments(user_comments: list[UserComment]) -> str:
    """Combine `user_comments` into a single string that looks like this:
    <username_1>: "user 1's comment" | <username_2>: "user 2's comment" | <username_1>: "user 1's comment"
    """
    return " | ".join([str(comment) for comment in user_comments])


def get_filtered_sessions(request, experiment, query_params, include_api=False):
    from apps.channels.models import ChannelPlatform

    sessions_queryset = (
        ExperimentSession.objects.with_last_message_created_at()
        .filter(experiment=experiment)
        .select_related("participant__user")
    )

    if not include_api:
        sessions_queryset = sessions_queryset.exclude(experiment_channel__platform=ChannelPlatform.API)
    sessions_queryset = apply_dynamic_filters(sessions_queryset, request, parsed_params=query_params)

    return sessions_queryset


def filtered_export_to_csv(experiment, sessions_queryset):
    csv_in_memory = io.StringIO()
    writer = csv.writer(csv_in_memory, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)

    queryset = sessions_queryset.prefetch_related(
        "chat",
        "chat__messages",
        "participant",
        "experiment_channel",
        "chat__tags",
        "chat__messages__tags",
        "chat__messages__comments",
        "chat__messages__comments__user",
    )
    header = [
        "Message ID",
        "Message Date",
        "Message Type",
        "Message Content",
        "Platform",
        "Chat Tags",
        "Chat Comments",
        "Session ID",
        "Session LLM",
        "Experiment ID",
        "Experiment Name",
        "Participant Name",
        "Participant Identifier",
        "Participant Public ID",
        "Message Tags",
        "Message Comments",
        "Trace ID",
    ]
    writer.writerow(header)

    for session in queryset:
        for message in session.chat.messages.all():
            trace_id = message.trace_info.get("trace_id", "") if message.trace_info else ""
            row = [
                message.id,
                message.created_at,
                message.message_type,
                message.content,
                session.get_platform_name(),
                _format_tags(message.chat.tags.all()),
                _format_comments(message.chat.comments.all()),
                session.external_id,
                experiment.get_llm_provider_model_name(raises=False),
                experiment.public_id,
                experiment.name,
                session.participant.name,
                session.participant.identifier,
                session.participant.public_id,
                _format_tags(message.tags.all()),
                _format_comments(message.comments.all()),
                trace_id,
            ]
            writer.writerow(row)
    return csv_in_memory
