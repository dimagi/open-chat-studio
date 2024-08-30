from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from taggit.serializers import TaggitSerializer, TagListSerializerField

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.files.models import File
from apps.teams.models import Team


class ExperimentSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="api:experiment-detail", lookup_field="public_id", lookup_url_kwarg="id", label="API URL"
    )
    id = serializers.UUIDField(source="public_id")

    class Meta:
        model = Experiment
        fields = ["id", "name", "url"]


class ParticipantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Participant
        fields = ["identifier"]


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["name", "slug"]


class FileSerializer(serializers.ModelSerializer):
    size = serializers.IntegerField(source="content_size")

    class Meta:
        model = File
        fields = ("name", "content_type", "size")


class MessageSerializer(TaggitSerializer, serializers.ModelSerializer):
    role = serializers.ChoiceField(choices=["system", "user", "assistant"], source="message_type")
    content = serializers.CharField()
    metadata = serializers.JSONField(required=False, read_only=True)
    tags = TagListSerializerField(read_only=True)
    attachments = serializers.ListField(source="get_attached_files", child=FileSerializer(), read_only=True)

    class Meta:
        model = ChatMessage
        fields = ["role", "content", "metadata", "tags", "attachments"]

    def to_representation(self, instance):
        if instance.is_summary:
            # summary isn't saved to the DB, so you can query for tags
            instance.tags = []
        data = super().to_representation(instance)
        data["role"] = ChatMessageType(data["role"]).role
        for key in ChatMessage.INTERNAL_METADATA_KEYS:
            data["metadata"].pop(key, None)
        return data

    def to_internal_value(self, data):
        data = super().to_internal_value(data)
        data["message_type"] = ChatMessageType.from_role(data["message_type"])
        return data


class ExperimentSessionSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="api:session-detail", lookup_field="external_id", lookup_url_kwarg="id"
    )
    id = serializers.ReadOnlyField(source="external_id")
    team = TeamSerializer(read_only=True)
    experiment = ExperimentSerializer(read_only=True)
    participant = ParticipantSerializer(read_only=True)
    messages = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentSession
        fields = ["url", "id", "team", "experiment", "participant", "created_at", "updated_at", "messages"]

    def __init__(self, *args, **kwargs):
        self._include_messages = kwargs.pop("include_messages", False)
        super().__init__(*args, **kwargs)
        if not self._include_messages:
            self.fields.pop("messages")
        else:
            # hack to change the component name for the schema to include messages
            self._spectacular_annotation = {"component_name": "ExperimentSessionWithMessages"}

    @extend_schema_field(MessageSerializer(many=True))
    def get_messages(self, instance):
        messages = list(instance.chat.message_iterator())
        return MessageSerializer(reversed(messages), many=True).data


class ExperimentSessionCreateSerializer(serializers.ModelSerializer):
    experiment = serializers.SlugRelatedField(
        slug_field="public_id", queryset=Experiment.objects, label="Experiment ID"
    )
    participant = serializers.CharField(
        required=False, label="Participant identifier", help_text="Channel specific participant identifier"
    )
    messages = MessageSerializer(many=True, required=False)

    class Meta:
        model = ExperimentSession
        fields = ["url", "experiment", "participant", "messages"]

    @transaction.atomic
    def create(self, validated_data):
        request = self.context["request"]
        experiment = validated_data["experiment"]
        if experiment.team_id != request.team.id:
            raise NotFound("Experiment not found")
        validated_data["team"] = request.team
        participant_identifier = validated_data.get("participant", request.user.email)
        participant, _created = Participant.objects.get_or_create(
            identifier=participant_identifier, team=request.team, user=request.user, platform=ChannelPlatform.API
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

    def validate(self, data):
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
    identifier = serializers.CharField(label="Participant identifier")
    platform = serializers.ChoiceField(
        choices=ChannelPlatform.choices, default=ChannelPlatform.API, label="Participant Platform"
    )
    data = ParticipantExperimentData(many=True)
