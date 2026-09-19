"""The chatbot version endpoints (#4142).

Every write in this API targets the working (draft) version. Creating a version is how that draft
becomes an immutable snapshot, which a client verifies by reading it back rather than by editing
it. A worker takes the snapshot, so the request answers `202` rather than with the version, and the
second endpoint here reports the chatbot's version history as that version lands in it.
"""

from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from field_audit.models import AuditAction
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import BASE_PERMISSION_CLASSES, RequiresTeamPermission
from apps.api.v2.lookups import get_working_chatbot
from apps.api.v2.write.base import ChatbotCompositionPermission
from apps.experiments.models import Experiment
from apps.experiments.tasks import start_version_creation
from apps.oauth.permissions import TokenHasOAuthResourceScope, enforce_application_chatbot_write
from apps.pipelines.build_state import pipeline_build_state

from .exceptions import NothingToPublish, PipelineIsNotValid, VersionIsDefault, VersionOperationInProgress
from .serializers import (
    PublishVersionSerializer,
    VersionArchivedSerializer,
    VersionCreateRefusedSerializer,
    VersionCreateSerializer,
    VersionPublishedSerializer,
    VersionStatus,
    VersionStatusSerializer,
)

CHATBOT_ID = OpenApiParameter(
    name="id", type=OpenApiTypes.UUID, location=OpenApiParameter.PATH, description="Chatbot ID"
)
VERSION_NUMBER = OpenApiParameter(
    name="version_number",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    description="The version's number, as the `chatbot_inspect` endpoint reports it.",
)
FORBIDDEN = OpenApiResponse(
    description=(
        "The caller is authenticated but not authorised for this chatbot's versions: either its "
        "role lacks permission to change chatbots, or it is a machine (client-credentials) token "
        "whose application is not authorised for this chatbot."
    )
)
FORBIDDEN_DELETE = OpenApiResponse(
    description=(
        "The caller is authenticated but not authorised to archive this chatbot's versions: either "
        "its role lacks permission to delete chatbots, or it is a machine (client-credentials) "
        "token whose application is not authorised for this chatbot."
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
        # `create_new_version` grants default status unconditionally to a chatbot's first version
        # -- one holding versions but no default would serve nothing -- so the gate covers that as
        # well as an asked-for one. Read from the working row's own counter, which every publish
        # increments, so archiving versions does not make a later publish look like a first one.
        if make_default or chatbot.version_number == 1:
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


class ChatbotVersionDeletePermission(RequiresTeamPermission):
    """Archiving a version really is a delete, so this one asks for ``delete_experiment``.

    The other routes under ``/chatbots/{id}/`` deliberately do not: removing a pipeline node is a
    change to the chatbot rather than a deletion of one. A version is an ``Experiment`` row of its
    own, and this hides it.
    """

    required_permissions = ["experiments.delete_experiment"]


class ChatbotVersionView(APIView):
    """The two writes that address one published version: promote it, or archive it."""

    # Set per verb by `get_permissions`; declared so a misconfiguration is a refusal, not an
    # open door, if that override is ever removed.
    permission_classes = [*BASE_PERMISSION_CLASSES, ChatbotVersionDeletePermission, TokenHasOAuthResourceScope]
    required_scopes = ["chatbots"]

    def get_permissions(self):
        """Archiving a version really is a delete; promoting one is a change to the chatbot.

        The two verbs share a path, so the permission cannot be a class attribute: `delete_version`
        would refuse a role that may edit chatbots but not delete them the right to choose which
        version is served, and `change_experiment` alone would let it hide versions.
        """
        write = ChatbotVersionDeletePermission if self.request.method == "DELETE" else ChatbotCompositionPermission
        return [permission() for permission in [*BASE_PERMISSION_CLASSES, write, TokenHasOAuthResourceScope]]

    @extend_schema(
        operation_id="chatbot_version_publish",
        summary="Make chatbot version the published one",
        description=(
            "Make this version the one participants are served, without snapshotting anything.\n\n"
            "Channels resolve the published version per message, so a live chatbot switches over "
            "from the next message on. The version that held it stops being served and is "
            "otherwise untouched -- a chatbot holds exactly one published version, so promoting "
            "one demotes the other.\n\n"
            "This is how an already-published version is served again; the "
            "`chatbot_version_create` endpoint's `make_default` covers the other case, a snapshot "
            "that goes live as it is taken. Promoting a version this way creates nothing, so it is "
            "never refused for having no changes to record."
        ),
        tags=["Chatbots"],
        parameters=[CHATBOT_ID, VERSION_NUMBER],
        request=PublishVersionSerializer,
        responses={
            200: VersionPublishedSerializer,
            400: OpenApiResponse(
                description=(
                    "The body carries a key this endpoint does not accept, or asks to un-publish a "
                    "version -- a chatbot always needs a published one."
                )
            ),
            403: FORBIDDEN,
            404: OpenApiResponse(
                description=(
                    "No such chatbot, or it has no version under this number -- an archived "
                    "chatbot and an archived version included."
                )
            ),
        },
    )
    def patch(self, request, id: str, version_number: int) -> Response:
        body = PublishVersionSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        # One transaction and one row lock on the working chatbot, because demoting the incumbent
        # and promoting this version have to land together: interleaved, two promotions could
        # demote each other's winner and leave the chatbot with no published version at all.
        with transaction.atomic():
            chatbot = get_working_chatbot(request.team, id, lock=True)
            enforce_application_chatbot_write(request, chatbot)
            version = get_object_or_404(chatbot.versions, version_number=version_number)
            _publish_existing_version(chatbot, version)
        return Response({"version_number": version.version_number, "is_published_version": True})

    @extend_schema(
        operation_id="chatbot_version_archive",
        summary="Archive a chatbot version",
        description=(
            "Archive one of the chatbot's versions. This is a soft delete: the version and the pipeline "
            "snapshot it owns are hidden rather than destroyed, and a person can restore it in the "
            "web app. Nothing in this API reaches an archived version, so a repeat of this call "
            "answers `404` -- which makes it safe to retry after an answer you never saw.\n\n"
            "**The published version is refused with `409`.** It is the version participants are "
            "served and a chatbot may hold only one, so archiving it would leave the chatbot with "
            "none; the `chatbot_version_publish` endpoint moves it to another version first.\n\n"
            "The working (draft) version has no number and is not reachable here. Archiving the "
            "whole chatbot is the `chatbot_archive` endpoint, which takes the channel guard and "
            "the scheduled messages with it -- neither of which a single version has."
        ),
        tags=["Chatbots"],
        parameters=[CHATBOT_ID, VERSION_NUMBER],
        request=None,
        responses={
            200: VersionArchivedSerializer,
            403: FORBIDDEN_DELETE,
            404: OpenApiResponse(
                description=(
                    "No such chatbot, or it has no version under this number -- an archived "
                    "chatbot and an already-archived version included."
                )
            ),
            409: OpenApiResponse(description="This is the chatbot's published version."),
        },
    )
    def delete(self, request, id: str, version_number: int) -> Response:
        # Resolution, the default-version rule and the archive share one transaction and one row
        # lock on the working chatbot, so two archives of the same version cannot both read it as
        # live. The second blocks, then re-resolves against the committed `is_archived` and 404s.
        with transaction.atomic():
            chatbot = get_working_chatbot(request.team, id, lock=True)
            # A machine token reaches only the chatbots its application was pinned to. Checked after
            # resolution because the allowlist is keyed on the chatbot, and before anything written.
            enforce_application_chatbot_write(request, chatbot)
            # `versions` excludes archived rows, so an already-archived version is a 404 here.
            version = get_object_or_404(chatbot.versions, version_number=version_number)
            if version.is_default_version:
                raise VersionIsDefault()
            version.archive()
        return Response({"archived": True})


def _publish_existing_version(chatbot: Experiment, version: Experiment) -> None:
    """Move the published flag onto ``version``, taking it off whichever version holds it.

    A partial unique constraint allows one published version per family, so the incumbent is
    demoted in the same statement-pair under the caller's row lock rather than left to collide.
    Already-published is not an error: the request asked for a state that already holds.
    """
    chatbot.versions.filter(is_default_version=True).exclude(id=version.id).update(
        is_default_version=False, audit_action=AuditAction.AUDIT
    )
    if not version.is_default_version:
        version.is_default_version = True
        version.save(update_fields=["is_default_version"])


def _version_status(chatbot: Experiment) -> dict:
    """Where ``chatbot``'s version history stands: an operation in flight, or the newest version."""
    if chatbot.version_operation_in_progress:
        return {"status": VersionStatus.PENDING}
    version = chatbot.latest_version
    if version is None:
        raise NotFound("This chatbot has no versions yet.")
    return {"status": VersionStatus.COMPLETED, "version_number": version.version_number}
