import textwrap

from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from taggit.serializers import TaggitSerializer, TagListSerializerField

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import ChatMessage, ChatMessageMetadataKeys, ChatMessageType
from apps.cost_tracking.services.reporting import session_usage
from apps.experiments.models import Experiment, ExperimentSession, Participant, ParticipantData
from apps.files.models import File
from apps.teams.models import Team


class ApiUrlField(serializers.HyperlinkedIdentityField):
    """``HyperlinkedIdentityField`` that documents a concrete ``example`` URL in the OpenAPI schema.

    Without an example, ReDoc samples a ``format: uri`` field as ``http://example.com``. The schema
    (with the example) is emitted by ``apps.api.schema.ApiUrlFieldExtension``; the placeholder host
    in ``openapi_example`` is then rewritten to the serving deployment's host by
    ``apps.api.schema.set_example_urls``.

    ``openapi_example`` is kept off ``_kwargs`` (so ``HyperlinkedIdentityField.__init__`` doesn't
    reject it) and re-applied in ``__deepcopy__``, since DRF clones declared fields per serializer
    instance by re-instantiating from ``_args``/``_kwargs`` alone.
    """

    def __init__(self, *args, openapi_example: str = "", **kwargs):
        self.openapi_example = openapi_example
        super().__init__(*args, **kwargs)

    def __deepcopy__(self, memo):
        clone = super().__deepcopy__(memo)
        clone.openapi_example = self.openapi_example
        return clone


class ExperimentVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Experiment
        fields = ["name", "version_number", "is_default_version", "version_description"]


class ExperimentSerializer(serializers.ModelSerializer):
    url = ApiUrlField(
        openapi_example="https://example.com/api/experiments/123e4567-e89b-12d3-a456-426614174000/",
        view_name="api:experiment-detail",
        lookup_field="public_id",
        lookup_url_kwarg="id",
        label="API URL",
    )
    id = serializers.UUIDField(source="public_id")
    versions = ExperimentVersionSerializer(many=True)

    class Meta:
        model = Experiment
        fields = ["id", "name", "url", "version_number", "versions"]


class ExperimentStubSerializer(serializers.ModelSerializer):
    """A lightweight experiment representation for embedding in other resources."""

    url = ApiUrlField(
        openapi_example="https://example.com/api/experiments/123e4567-e89b-12d3-a456-426614174000/",
        view_name="api:experiment-detail",
        lookup_field="public_id",
        lookup_url_kwarg="id",
        label="API URL",
    )
    id = serializers.UUIDField(source="public_id")

    class Meta:
        model = Experiment
        fields = ["id", "name", "description", "url", "version_number"]


class ParticipantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Participant
        fields = ["identifier", "remote_id"]


class ParticipantDataEntrySerializer(serializers.ModelSerializer):
    chatbot = serializers.CharField(source="experiment.name", read_only=True)
    chatbot_id = serializers.UUIDField(source="experiment.public_id", read_only=True)
    connect_channel_id = serializers.SerializerMethodField(
        help_text="CommCare Connect channel ID. Only set for the CommCare Connect channel."
    )

    class Meta:
        model = ParticipantData
        fields = ["chatbot", "chatbot_id", "data", "connect_channel_id"]

    def get_connect_channel_id(self, obj) -> str | None:
        return obj.system_metadata.get("commcare_connect_channel_id")


class ParticipantDetailSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    data = serializers.SerializerMethodField()

    class Meta:
        model = Participant
        fields = ["id", "identifier", "name", "platform", "remote_id", "data"]

    @extend_schema_field(ParticipantDataEntrySerializer(many=True))
    def get_data(self, obj):
        # queryset is pre-annotated via the view's get_queryset / prefetch
        qs = getattr(obj, "_prefetched_participant_data", None)
        if qs is None:
            qs = obj.data_set.filter(team=obj.team).select_related("experiment")
        return ParticipantDataEntrySerializer(qs, many=True).data


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["name", "slug"]


class FileSerializer(serializers.ModelSerializer):
    size = serializers.IntegerField(source="content_size")
    content_url = ApiUrlField(
        openapi_example="https://example.com/api/files/42/content",
        view_name="api:file-content",
        lookup_field="id",
        lookup_url_kwarg="pk",
    )

    class Meta:
        model = File
        fields = ("name", "content_type", "size", "content_url")


class MessageSerializer(TaggitSerializer, serializers.ModelSerializer):
    created_at = serializers.DateTimeField(read_only=True, required=False)
    role = serializers.ChoiceField(choices=["system", "user", "assistant"], source="message_type")
    content = serializers.CharField()
    metadata = serializers.JSONField(
        required=False,
        read_only=True,
        help_text=textwrap.dedent(
            """
            Metadata for the message. Currently documented keys:
              * `compression_marker`: Slug that marks this message as a checkpoint for session history compression
            """
        ),
    )
    tags = TagListSerializerField(read_only=True)
    attachments = serializers.ListField(source="get_attached_files", child=FileSerializer(), read_only=True)

    class Meta:
        model = ChatMessage
        fields = ["created_at", "role", "content", "metadata", "tags", "attachments"]

    def to_representation(self, instance):
        if not instance.pk:
            # don't try and load tags if it isn't saved to the DB e.g. summary messages
            instance.tags = []
        data = super().to_representation(instance)
        data["role"] = ChatMessageType(data["role"]).role
        for key in ChatMessageMetadataKeys.internal_keys():
            data["metadata"].pop(key, None)
        return data

    def to_internal_value(self, data):
        data = super().to_internal_value(data)
        data["message_type"] = ChatMessageType.from_role(data["message_type"])
        return data


class SessionModelUsageSerializer(serializers.Serializer):
    model_name = serializers.CharField()
    cost = serializers.DecimalField(max_digits=14, decimal_places=8)
    tokens = serializers.IntegerField()


class SessionUsageSerializer(serializers.Serializer):
    total_cost = serializers.DecimalField(max_digits=14, decimal_places=8)
    by_model = SessionModelUsageSerializer(many=True)


class ExperimentSessionSerializer(serializers.ModelSerializer):
    url = ApiUrlField(
        openapi_example="https://example.com/api/sessions/123e4567-e89b-12d3-a456-426614174000/",
        view_name="api:session-detail",
        lookup_field="external_id",
        lookup_url_kwarg="id",
    )
    id = serializers.ReadOnlyField(source="external_id")
    team = TeamSerializer(read_only=True)
    experiment = ExperimentStubSerializer(read_only=True)
    participant = ParticipantSerializer(read_only=True)
    messages = serializers.SerializerMethodField()
    usage = serializers.SerializerMethodField()
    tags = TagListSerializerField(source="chat.tags")

    class Meta:
        model = ExperimentSession
        fields = [
            "url",
            "id",
            "team",
            "experiment",
            "participant",
            "created_at",
            "updated_at",
            "status",
            "platform",
            "messages",
            "usage",
            "tags",
            "state",
        ]

    def __init__(self, *args, **kwargs):
        self._include_messages = kwargs.pop("include_messages", False)
        super().__init__(*args, **kwargs)
        if not self._include_messages:
            self.fields.pop("messages")
            self.fields.pop("usage")
        else:
            # hack to change the component name for the schema to include messages
            self._spectacular_annotation = {"component_name": "ExperimentSessionWithMessages"}

    @extend_schema_field(MessageSerializer(many=True))
    def get_messages(self, instance):
        messages = list(instance.chat.message_iterator())
        return MessageSerializer(reversed(messages), many=True, context=self.context).data

    @extend_schema_field(SessionUsageSerializer)
    def get_usage(self, instance):
        return SessionUsageSerializer(session_usage(instance)).data


class ExperimentSessionCreateSerializer(serializers.ModelSerializer):
    experiment = serializers.SlugRelatedField(
        slug_field="public_id", queryset=Experiment.objects, label="Experiment ID"
    )
    participant = serializers.CharField(
        required=False, label="Participant identifier", help_text="Channel specific participant identifier"
    )
    messages = MessageSerializer(many=True, required=False)
    state = serializers.JSONField(required=False)

    class Meta:
        model = ExperimentSession
        fields = ["url", "experiment", "participant", "messages", "state"]

    @transaction.atomic
    def create(self, validated_data):
        request = self.context["request"]
        experiment = validated_data["experiment"]
        if experiment.team_id != request.team.id:
            raise NotFound("Experiment not found")
        validated_data["team"] = request.team
        participant_identifier = validated_data.get("participant", request.user.email)
        participant, _created = Participant.objects.get_or_create(
            identifier=participant_identifier,
            team=request.team,
            platform=ChannelPlatform.API,
            defaults={"user": request.user},
        )
        validated_data["participant"] = participant
        channel = ExperimentChannel.objects.get_team_api_channel(request.team)
        validated_data["experiment_channel"] = channel
        messages = validated_data.pop("messages", [])
        instance = super().create(validated_data)
        if messages:
            ChatMessage.objects.bulk_create([ChatMessage(chat=instance.chat, **message) for message in messages])
        return instance


class ParticipantScheduleSerializer(serializers.Serializer):
    id = serializers.CharField(label="Schedule ID", required=False, max_length=32)
    name = serializers.CharField(label="Schedule Name", required=False)
    prompt = serializers.CharField(label="Prompt to send to bot", required=False)
    date = serializers.DateTimeField(label="Schedule Date", required=False)
    delete = serializers.BooleanField(label="Delete Schedule", required=False, default=False)

    def validate(self, data):  # ty: ignore[invalid-method-override]
        if data.get("delete"):
            if not data.get("id"):
                raise serializers.ValidationError("Schedule ID is required to delete a schedule")
        elif not all([data.get("name"), data.get("prompt"), data.get("date")]):
            raise serializers.ValidationError(
                "Schedule Name, Prompt, and Date are required to create or update a schedule"
            )
        return data


class ParticipantExperimentData(serializers.Serializer):
    experiment = serializers.UUIDField(label="Experiment ID")
    data = serializers.DictField(label="Participant Data", required=False)
    schedules = ParticipantScheduleSerializer(many=True, required=False)


class ParticipantDataUpdateRequest(serializers.Serializer):
    identifier = serializers.CharField(label="Participant Identifier")
    name = serializers.CharField(label="Participant Name", required=False)
    platform = serializers.ChoiceField(
        choices=ChannelPlatform.choices, default=ChannelPlatform.API, label="Participant Platform"
    )
    data = ParticipantExperimentData(many=True)


class ChatStartSessionRequest(serializers.Serializer):
    chatbot_id = serializers.UUIDField(label="Chatbot ID")
    version_number = serializers.IntegerField(
        label="Version Number",
        required=False,
        allow_null=True,
        help_text="Optional version number of the chatbot to use. Requires authentication.",
    )
    session_data = serializers.DictField(
        label="Initial session data",
        required=False,
        help_text="Optional initial state data for the session. "
        "This field will be ignored if the request is not authenticated.",
    )
    participant_remote_id = serializers.CharField(
        label="Participant Remote Id",
        required=False,
        help_text="Optional ID for the participant from remote systems",
        default="",
    )
    participant_name = serializers.CharField(
        label="Paricipant Name", required=False, help_text="Optional participant name"
    )
    use_session_token = serializers.BooleanField(
        label="Use session token",
        required=False,
        allow_null=True,
        default=None,
        help_text="Whether to protect the session with a session token (default true). When enabled, the"
        " response includes a `session_token` which must be sent as the `X-Session-Token` header on all"
        " subsequent requests for this session. Set to false to opt out and rely on legacy access rules.",
    )


class ChatStartSessionResponse(serializers.Serializer):
    session_id = serializers.UUIDField(label="Session ID")
    session_token = serializers.CharField(
        label="Session token",
        allow_null=True,
        required=False,
        help_text="Present when the session is token-protected. Send as the `X-Session-Token` header on all"
        " subsequent requests for this session.",
    )
    chatbot = ExperimentSerializer(read_only=True)
    participant = ParticipantSerializer(read_only=True)


class ChatSendMessageRequest(serializers.Serializer):
    message = serializers.CharField(label="Message content")
    context = serializers.DictField(
        label="Context",
        required=False,
        help_text=(
            "Additional context data to include with the message such as details about the page the user is on"
            " or the action they are trying to perform. This is stored in the session state under the"
            " `remote_context` key."
        ),
    )


class ChatSendMessageResponse(serializers.Serializer):
    task_id = serializers.CharField(label="Task ID", help_text="The ID of the task that is processing the message")
    status = serializers.ChoiceField(
        choices=[("processing", "Processing"), ("completed", "Completed"), ("error", "Error")],
        label="Processing status",
    )
    error = serializers.CharField(label="Error message", required=False)


class ChatPollResponse(serializers.Serializer):
    messages = MessageSerializer(many=True, label="New messages since last poll")
    has_more = serializers.BooleanField(label="Whether there are more messages to fetch")
    session_status = serializers.ChoiceField(
        choices=[("active", "Active"), ("ended", "Ended")], label="Current session status"
    )


class TriggerBotMessageRequest(serializers.Serializer):
    identifier = serializers.CharField(label="Participant Identifier")
    platform = serializers.ChoiceField(choices=ChannelPlatform.choices, label="Participant Platform")
    experiment = serializers.UUIDField(label="Experiment ID")
    prompt_text = serializers.CharField(label="Prompt to go to bot", required=False, allow_null=True, default=None)
    message_text = serializers.CharField(
        label="Message to send directly to participant (bypasses bot/LLM)",
        required=False,
        allow_null=True,
        default=None,
    )
    start_new_session = serializers.BooleanField(label="Starts a new session", required=False, default=False)
    session_data = serializers.DictField(
        help_text="Update session data. This will be merged with existing session data", required=False, default=dict
    )
    participant_data = serializers.DictField(
        help_text="Update Participant data. This will be merged with existing participant data",
        required=False,
        default=dict,
    )

    def validate(self, data):  # ty: ignore[invalid-method-override]
        has_prompt = bool(data.get("prompt_text"))
        has_message = bool(data.get("message_text"))
        if has_prompt and has_message:
            raise serializers.ValidationError("Provide either 'prompt_text' or 'message_text', not both.")
        if not has_prompt and not has_message:
            raise serializers.ValidationError("Either 'prompt_text' or 'message_text' must be provided.")
        return data


class TriggerBotMessageResponse(serializers.ModelSerializer):
    session_id = serializers.ReadOnlyField(source="external_id")
    url = ApiUrlField(
        openapi_example="https://example.com/api/sessions/123e4567-e89b-12d3-a456-426614174000/",
        view_name="api:session-detail",
        lookup_field="external_id",
        lookup_url_kwarg="id",
    )
    team = TeamSerializer(read_only=True)
    channel = serializers.CharField(source="platform", read_only=True)

    class Meta:
        model = ExperimentSession
        fields = ["session_id", "url", "team", "channel"]
