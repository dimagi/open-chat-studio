"""What the chatbot write endpoints refuse a request with, and why."""

from rest_framework import status
from rest_framework.exceptions import APIException

from apps.api.v2.write.serializers import AttachedChannelSerializer
from apps.channels.models import ExperimentChannel


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
