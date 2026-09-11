"""What the chatbot version endpoints refuse a request with, and why (#4142)."""

from rest_framework import status
from rest_framework.exceptions import APIException


class VersionOperationInProgress(APIException):
    """Another version operation holds the chatbot's version lock.

    The lock covers reverting as well as creating a version, so the answer names neither a task to
    poll nor which operation holds it: a revert's lock token is not a Celery task id, and handing
    one out as a poll handle would report a version that was never created.
    """

    status_code = status.HTTP_409_CONFLICT
    default_detail = "A version operation is already in progress for this chatbot. Try again once it has finished."


class PipelineIsNotValid(APIException):
    """The pipeline has errors, so the snapshot may not become the version participants are served.

    422 rather than 400: the body was fine and the request was understood -- it is the state of the
    chatbot that makes it unanswerable.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY

    def __init__(self, pipeline_errors: dict) -> None:
        super().__init__(
            {
                "detail": (
                    "This chatbot's pipeline has errors, so it cannot be made the version "
                    "participants are served. Repair what `pipeline_errors` reports, or leave out "
                    "`make_default` to checkpoint the work as it stands."
                ),
                "pipeline_errors": pipeline_errors,
            }
        )


class NothingToPublish(APIException):
    """The working version is identical to the newest one, so a new version would record no change.

    422 for the same reason as ``PipelineIsNotValid``: the body was fine and the request understood
    -- it is the state of the chatbot that makes it unanswerable.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = (
        "This chatbot has no changes since its newest version, so there is nothing to snapshot. "
        "Write to the working version first, or read the newest version back with the "
        "`chatbot_inspect` endpoint's `version` parameter."
    )


class VersionIsDefault(APIException):
    """The version participants are served cannot be archived out from under them.

    409 rather than 403: the caller may archive this chatbot's versions, and will be able to archive
    this one, once it is no longer the default. Retrying without that changing will not help.
    """

    status_code = status.HTTP_409_CONFLICT
    default_detail = (
        "This is the published version, the one participants are served, and a chatbot may hold "
        "only one -- so archiving it would leave the chatbot with none. Make another version the "
        "published one first, with the `chatbot_version_publish` endpoint or in the web app."
    )
