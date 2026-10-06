"""What the content endpoints refuse a request with, and why."""

from rest_framework import status
from rest_framework.exceptions import APIException


class ArchiveRefused(APIException):
    """The resource cannot be archived while it is in its current state."""

    status_code = status.HTTP_409_CONFLICT


class SourceMaterialInUse(ArchiveRefused):
    """The source material is still used by pipeline nodes."""

    def __init__(self, references: dict) -> None:
        # Assigned rather than passed to ``super().__init__``, which would turn the nested ids and
        # booleans into strings.
        self.detail = {
            "detail": (
                "This source material is still used by the pipeline nodes listed below. Remove it from "
                "each draft's nodes, then archive again. A published version keeps using it until the "
                "chatbot is published again without it, and references under `other_pipelines` can "
                "only be removed in the web app."
            ),
            **references,
        }
