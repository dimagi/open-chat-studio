"""The chatbot version endpoints (#4142).

Every write in this API targets the working (draft) version. Creating a version is how that draft
becomes an immutable snapshot, which a client verifies by reading it back rather than by editing
it. A worker takes the snapshot, so the request answers `202` rather than with the version, and the
second endpoint here reports the chatbot's version history as that version lands in it.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import BASE_PERMISSION_CLASSES
from apps.api.v2.lookups import get_working_chatbot
from apps.api.v2.write.base import ChatbotCompositionPermission
from apps.experiments.models import Experiment
from apps.experiments.tasks import start_version_creation
from apps.oauth.permissions import TokenHasOAuthResourceScope, enforce_application_chatbot_write
from apps.pipelines.build_state import pipeline_build_state

from .exceptions import NothingToPublish, PipelineIsNotValid, VersionOperationInProgress
from .serializers import (
    VersionCreateRefusedSerializer,
    VersionCreateSerializer,
    VersionStatus,
    VersionStatusSerializer,
)

CHATBOT_ID = OpenApiParameter(
    name="id", type=OpenApiTypes.UUID, location=OpenApiParameter.PATH, description="Chatbot ID"
)
FORBIDDEN = OpenApiResponse(
    description=(
        "The caller is authenticated but not authorised for this chatbot's versions: either its "
        "role lacks permission to change chatbots, or it is a machine (client-credentials) token "
        "whose application is not authorised for this chatbot."
    )
)


class ChatbotVersionCreateView(APIView):
    """Snapshot the working version as a new immutable version."""

    permission_classes = [*BASE_PERMISSION_CLASSES, ChatbotCompositionPermission, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]

    @extend_schema(
        operation_id="chatbot_version_create",
        summary="Create a new version",
        description=(
            "Snapshot the chatbot's working (draft) version as a new, immutable version. Nothing "
            "writes to a version: read it back with the `chatbot_inspect` endpoint's `version` "
            "parameter, and every other write endpoint keeps targeting the working version.\n\n"
            "A worker takes the snapshot, so the answer is `202` with no body. Poll the "
            "`chatbot_version_status` endpoint for the version number and the outcome.\n\n"
            "One version operation runs at a time per chatbot, creating a version and reverting "
            "alike; a second is refused with `409`."
        ),
        tags=["Chatbots"],
        parameters=[CHATBOT_ID],
        request=VersionCreateSerializer,
        responses={
            202: OpenApiResponse(
                description="The version creation was accepted. Poll `chatbot_version_status` for its outcome."
            ),
            400: OpenApiResponse(description="The body carries a key this endpoint does not accept."),
            403: FORBIDDEN,
            404: OpenApiResponse(
                description=(
                    "No such chatbot. Archived chatbots and existing versions are both outside "
                    "this endpoint's reach: only a live working version can be snapshotted."
                )
            ),
            409: OpenApiResponse(
                description=(
                    "A version operation (another version creation, or a revert started in the "
                    "UI) is already running for this chatbot."
                )
            ),
            422: OpenApiResponse(
                response=VersionCreateRefusedSerializer,
                description=(
                    "Either the pipeline has errors and this version would be served to "
                    "participants -- `make_default: true`, or a chatbot's first version, which is "
                    "always the published one -- or the working version is identical to the newest "
                    "one, so the snapshot would record no change. Nothing is snapshotted either "
                    "way. Dangling handles are not errors: `unwired_handles` is advisory."
                ),
            ),
        },
    )
    def post(self, request, id: str) -> Response:
        body = VersionCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        chatbot = get_working_chatbot(request.team, id)
        # A machine token reaches only the chatbots its application was pinned to. After resolution
        # because the allowlist is keyed on the chatbot, and before the gate, which reads the graph.
        enforce_application_chatbot_write(request, chatbot)
        make_default = body.validated_data["make_default"]
        # The cheaper question first: an unchanged chatbot is refused for having nothing to publish
        # without the graph also being walked to find fault with it.
        _check_there_is_something_to_publish(chatbot)
        if chatbot.publish_goes_live(make_default=make_default):
            _check_pipeline_can_go_live(chatbot)
        dispatched = start_version_creation(
            chatbot,
            version_description=body.validated_data["version_description"],
            make_default=make_default,
        )
        # The task id it hands back goes nowhere: the status endpoint takes none. It is read only
        # for the `None` that means another version operation already holds the lock.
        if dispatched is None:
            raise VersionOperationInProgress()
        return Response(status=status.HTTP_202_ACCEPTED)


def _check_there_is_something_to_publish(chatbot: Experiment) -> None:
    """Refuse a version whose snapshot would be identical to the newest version.

    The comparison the web app's "unreleased changes" badge is drawn from, so the API and the web
    app agree about whether a chatbot has anything to publish.

    A chatbot with no versions has nothing to compare against, and ``compare_with_latest()`` answers
    ``False`` for it -- "no baseline" and "no difference" arrive as one value, so the absence of
    versions is asked about separately rather than read out of the comparison.
    """
    if chatbot.latest_version is None:
        return
    if chatbot.compare_with_latest():
        return
    raise NothingToPublish()


def _check_pipeline_can_go_live(chatbot: Experiment) -> None:
    """Refuse a version that would serve participants a pipeline that does not validate.

    A deliberate divergence from the pipeline builder, which lets a human publish a bot whose red
    markers they can see. An unattended client has nothing to look at, and the version goes live
    per-message, so the refusal is the only backstop.

    Judged on exactly what the façade writes and `chatbot_inspect` reads report, so the gate and the
    report cannot disagree about whether a pipeline is publishable.

    ``Experiment.pipeline`` is nullable and rows predating pipeline-backed chatbots hold null: there
    is nothing to validate, so there is nothing to refuse -- as in the UI.
    """
    if chatbot.pipeline_id is None:
        return
    state = pipeline_build_state(chatbot.pipeline)
    if not state["pipeline_valid"]:
        raise PipelineIsNotValid(state["errors"])


class ChatbotVersionStatusView(APIView):
    """Report where the chatbot's version history stands."""

    permission_classes = [*BASE_PERMISSION_CLASSES, ChatbotCompositionPermission, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]

    @extend_schema(
        operation_id="chatbot_version_status",
        summary="Version creation status",
        description=(
            "Report where this chatbot's version history stands, which is how a version the "
            "`chatbot_version_create` endpoint accepted is followed to its end.\n\n"
            "Poll this until `status` is terminal:\n\n"
            "- **`pending`** — a version operation is still running. Poll again.\n"
            "- **`completed`** — no operation is running and `version_number` names the chatbot's "
            "newest version. Read it back with the `chatbot_inspect` endpoint's `version` "
            "parameter to check what landed in it.\n\n"
            "**`completed` is not a receipt for your request.** This reports the chatbot's own "
            "state, not one task's outcome, so it cannot say which request produced the version it "
            "names. A version creation the worker raised on releases the lock without snapshotting "
            "anything, and polling then reports `completed` on the version that was already "
            "there — so check `version_number` against the one you expected rather than reading "
            "`completed` as proof the snapshot landed.\n\n"
            "A chatbot with no version at all has nothing to report and answers `404`."
        ),
        tags=["Chatbots"],
        parameters=[CHATBOT_ID],
        responses={
            200: VersionStatusSerializer,
            403: FORBIDDEN,
            404: OpenApiResponse(description="No such chatbot, or it has never created a version."),
        },
    )
    def get(self, request, id: str) -> Response:
        chatbot = get_working_chatbot(request.team, id)
        # The same allowlist the publish is held to: a machine token that may not publish a chatbot
        # has no publish of its own to follow.
        enforce_application_chatbot_write(request, chatbot)
        return Response(_version_status(chatbot))


def _version_status(chatbot: Experiment) -> dict:
    """Where ``chatbot``'s version history stands: an operation in flight, or the newest version."""
    if chatbot.version_operation_in_progress:
        return {"status": VersionStatus.PENDING}
    version = chatbot.latest_version
    if version is None:
        raise NotFound("This chatbot has no versions yet.")
    return {"status": VersionStatus.COMPLETED, "version_number": version.version_number}
