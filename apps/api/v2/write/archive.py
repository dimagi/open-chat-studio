"""Archiving a chatbot: the guard that stands in the way, and what archiving destroys (#4143).

Archiving through this API is "undo my own draft", not a teardown of a live bot. There is no
channel endpoint of any kind: detaching a channel fires a best-effort call to the upstream provider
to remove its webhook, and since this API cannot *create* a channel, a client that detached one
could never put it back. So a chatbot with a channel attached is refused, and a person detaches it.
"""

from rest_framework import serializers, status
from rest_framework.exceptions import APIException

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import Experiment


class AttachedChannelSerializer(serializers.Serializer):
    """One channel standing in the way, named the way the web app names it.

    Enough for a client to tell a person which channels to go and detach; there is nothing here to
    address, because no endpoint takes a channel.
    """

    id = serializers.IntegerField()
    # The same choice set the `chatbot_inspect` endpoint reports a channel's platform from, so a
    # client parses one enum across both.
    platform = serializers.ChoiceField(choices=ChannelPlatform.choices)
    name = serializers.CharField()


class ArchivedSerializer(serializers.Serializer):
    """The archive response: that it happened, and what it destroyed on the way."""

    archived = serializers.BooleanField(help_text="Always true; the failure cases are status codes.")
    cancelled_scheduled_messages = serializers.IntegerField(
        help_text=(
            "How many of the chatbot's scheduled messages were deleted, finished and "
            "already-cancelled ones included. They cannot be recovered."
        )
    )


class ChannelsAttachedSerializer(serializers.Serializer):
    """The 409: why the archive was refused, and what a person has to do about it."""

    detail = serializers.CharField()
    channels = AttachedChannelSerializer(many=True)


class ChannelsAttached(APIException):
    """The chatbot is still live on channels only a person can detach.

    409 rather than 403: the caller may archive this chatbot, and will be able to, once the
    channels are off it. Retrying without that changing will not help.
    """

    status_code = status.HTTP_409_CONFLICT

    def __init__(self, channels: list[ExperimentChannel]) -> None:
        # Assigned rather than handed to ``super().__init__``, which runs a structured detail
        # through ``_get_error_details`` and turns every leaf into an ``ErrorDetail`` -- a ``str``
        # subclass, so each channel's integer id would render as a string. Setting ``detail`` is
        # all that ``__init__`` does with it, and the exception handler serves it as the body.
        self.detail = {
            "detail": (
                f"This chatbot is still attached to {len(channels)} channel(s). Detaching one "
                "removes its webhook at the messaging provider, so a person has to do it in the "
                "web app -- this API has no channel endpoints. Archive again once they are "
                "detached."
            ),
            "channels": AttachedChannelSerializer(channels, many=True).data,
        }


def archive_chatbot(chatbot: Experiment) -> dict:
    """Soft-archive the chatbot, or refuse while a channel is still attached.

    The caller resolves the chatbot under a row lock and calls this inside that transaction: the
    schedules are counted before ``archive()`` deletes them, so two archives racing unlocked would
    both count the same rows and both claim to have deleted them. Serialised, the second finds the
    chatbot already archived and outside the working-chatbot queryset every write path resolves
    through, so it answers 404 -- which is also what makes a retry after an unseen answer safe.
    """
    if attached := attached_channels(chatbot):
        raise ChannelsAttached(attached)
    # Counted before the archive, not after: `archive()` hard-deletes these rows and reports
    # nothing, so this is the last moment they can be seen. Blocking on them instead would
    # deadlock -- there is no scheduled-message endpoint to drain them with -- and cancelling
    # future sends is the right consequence of archiving anyway, so the count is reported to keep
    # the destruction visible to whoever the client answers to.
    cancelled = chatbot.scheduled_messages.count()
    chatbot.archive()
    return {"archived": True, "cancelled_scheduled_messages": cancelled}


def attached_channels(chatbot: Experiment) -> list[ExperimentChannel]:
    """The channels that stand in the way of archiving ``chatbot``, oldest first.

    The team-wide API, web and evaluations channels are excluded: there is one of each per team,
    they carry no chatbot of their own, and nobody detaches them -- so a web or API bot, which is
    all this API can create, archives freely. Already-detached rows are excluded by the default
    manager, since detaching soft-deletes: counting those would leave a chatbot permanently
    unarchivable.
    """
    return list(
        ExperimentChannel.objects.filter(experiment_id=chatbot.id)
        .exclude(platform__in=ChannelPlatform.team_global_platforms())
        .order_by("id")
    )
