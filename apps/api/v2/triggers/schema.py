"""OpenAPI descriptions for the trigger endpoints."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view

from apps.api.v2.write.base import CHATBOT_ID
from apps.api.v2.write.serializers import ResourceArchivedSerializer

from .serializers import (
    BaseTriggerSerializer,
    ScheduledTriggerWriteSerializer,
    StaticTriggerWriteSerializer,
    TimeoutTriggerWriteSerializer,
)

TRIGGER_ID = OpenApiParameter(
    name="trigger_id",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    description="The trigger's id, as returned by a create or a list.",
)
BAD_REQUEST = OpenApiResponse(
    description=(
        "The body is not one this endpoint accepts. Errors are keyed by the field at fault, with the "
        "action's errors under `action` and its params' errors under `action.params`; an error about "
        "the combination of trigger and action is under `action.non_field_errors`. Nothing is saved."
    )
)
FORBIDDEN = OpenApiResponse(
    description=(
        "The caller is authenticated but not authorised to modify this chatbot: either its role "
        "lacks permission to change chatbots, or it is a read-only key, or a machine "
        "(client-credentials) token whose application is not authorised for this chatbot."
    )
)
CHATBOT_NOT_FOUND = OpenApiResponse(description="No such working chatbot in this team.")
TRIGGER_NOT_FOUND = OpenApiResponse(
    description=(
        "No such chatbot, or no trigger of this kind with this id on its working version. A "
        "published version's triggers and archived triggers are not reachable."
    )
)
PUBLISH_NOTE = (
    "The change applies to the working (draft) version; publish the chatbot for it to reach "
    "participants of the published version."
)
ACTION_NOTE = (
    "Each trigger runs one action. `action.params` depends on `action.action_type`; the table in the "
    "`params` field's description lists the keys each type takes."
)


def trigger_schemas(*, kind: str, serializer: type[BaseTriggerSerializer], description: str):
    """The OpenAPI descriptions of one kind of trigger's operations, as one decorator per route.

    The first covers list and create, the second retrieve, update and archive.
    """
    noun = f"{kind} trigger"
    title = noun.capitalize()

    def operation(name: str, summary: str, **kwargs):
        return extend_schema(
            operation_id=f"chatbot_{kind}_trigger_{name}", summary=summary, tags=["Chatbot Triggers"], **kwargs
        )

    list_schema = extend_schema_view(
        get=operation(
            "list",
            f"List {title}s",
            description=f"The working (draft) chatbot's {noun}s, oldest first. Archived ones are left out.",
            parameters=[CHATBOT_ID],
            responses={200: serializer(many=True), 404: CHATBOT_NOT_FOUND},
        ),
        post=operation(
            "create",
            f"Add a {title}",
            description=f"{description}\n\n{ACTION_NOTE}\n\n{PUBLISH_NOTE}",
            parameters=[CHATBOT_ID],
            responses={201: serializer, 400: BAD_REQUEST, 403: FORBIDDEN, 404: CHATBOT_NOT_FOUND},
        ),
    )
    detail_schema = extend_schema_view(
        get=operation(
            "retrieve",
            f"Retrieve a {title}",
            description=f"One {noun} on the working (draft) chatbot, in the shape the update endpoint accepts.",
            parameters=[CHATBOT_ID, TRIGGER_ID],
            responses={200: serializer, 404: TRIGGER_NOT_FOUND},
        ),
        patch=operation(
            "update",
            f"Update a {title}",
            description=(
                "Only the fields you send change. If you send `action`, its `params` replace the stored "
                "params, so send every param the action needs; leave `action_type` out to keep the "
                "current one. Read-only fields in the body are ignored, so a response can be sent back "
                f"as it is.\n\n{PUBLISH_NOTE}"
            ),
            parameters=[CHATBOT_ID, TRIGGER_ID],
            responses={200: serializer, 400: BAD_REQUEST, 403: FORBIDDEN, 404: TRIGGER_NOT_FOUND},
        ),
        delete=operation(
            "archive",
            f"Archive a {title}",
            description=(
                f"Archive the {noun}. This is a soft delete: a person can restore it in the web app. "
                "A repeat of this call answers `404`, which makes it safe to retry.\n\n" + PUBLISH_NOTE
            ),
            parameters=[CHATBOT_ID, TRIGGER_ID],
            request=None,
            responses={200: ResourceArchivedSerializer, 403: FORBIDDEN, 404: TRIGGER_NOT_FOUND},
        ),
    )
    return list_schema, detail_schema


static_trigger_list_schema, static_trigger_schema = trigger_schemas(
    kind="static",
    serializer=StaticTriggerWriteSerializer,
    description=(
        "A static trigger runs its action when a conversation event happens, such as a conversation "
        "starting or a participant sending a message. Two combinations are refused: a "
        "`new_bot_message` trigger cannot run `send_message_to_bot` or `schedule_trigger`, and a "
        "trigger on a conversation ending cannot run `end_conversation`."
    ),
)


timeout_trigger_list_schema, timeout_trigger_schema = trigger_schemas(
    kind="timeout",
    serializer=TimeoutTriggerWriteSerializer,
    description=(
        "A timeout trigger runs its action after the participant has been inactive for `delay` "
        "seconds, up to `total_num_triggers` times in one session."
    ),
)


scheduled_trigger_list_schema, scheduled_trigger_schema = trigger_schemas(
    kind="scheduled",
    serializer=ScheduledTriggerWriteSerializer,
    description=(
        "A scheduled trigger runs its action once, at a set date and time, for every open session "
        "of the published version (or only those active within `active_session_window_days`). The "
        "date and time must be in the future."
    ),
)
