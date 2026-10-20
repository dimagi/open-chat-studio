"""What the resource endpoints refuse a request with, and why."""

from rest_framework import status
from rest_framework.exceptions import APIException

from apps.api.v2.resources.serializers import SourceMaterialInUseSerializer


class ArchiveRefused(APIException):
    """The resource cannot be archived while it is in its current state."""

    status_code = status.HTTP_409_CONFLICT


class SourceMaterialInUse(ArchiveRefused):
    """The source material is still used by pipeline nodes."""

    def __init__(self, references: dict) -> None:
        # Assigned rather than passed to ``super().__init__``, which would turn the nested ids and
        # booleans into strings.
        self.detail = SourceMaterialInUseSerializer({"detail": _in_use_detail(references), **references}).data


def _in_use_detail(references: dict) -> str:
    """What stands in the way, and the step that clears each kind of reference listed."""
    chatbots = references["chatbots"]
    steps = ["This source material is still used by the pipeline nodes listed below."]
    if any(chatbot["is_working_version"] for chatbot in chatbots):
        steps.append("Remove it from the working version's nodes (`is_working_version: true`).")
    if any(chatbot["published"] for chatbot in chatbots):
        steps.append(
            "The published version (`published: true`) cannot be edited: once the working version no "
            "longer uses it, publish a new version with the `chatbot_version_create` endpoint and "
            "`make_default: true`, then archive the old one with the `chatbot_version_archive` endpoint."
        )
    if any(not chatbot["published"] and not chatbot["is_working_version"] for chatbot in chatbots):
        steps.append("Archive each other version listed with the `chatbot_version_archive` endpoint.")
    if references["other_pipelines"]:
        steps.append("Pipelines under `other_pipelines` can only be changed in the web app.")
    steps.append("Then archive again.")
    return " ".join(steps)
