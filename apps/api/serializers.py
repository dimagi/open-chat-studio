from rest_framework import serializers
from rest_framework.exceptions import NotFound

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.teams.models import Team


class ExperimentSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name="api:experiment-detail", lookup_field="public_id")
    experiment_id = serializers.UUIDField(source="public_id")

    class Meta:
        model = Experiment
        fields = ["experiment_id", "name", "url"]


class ParticipantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Participant
        fields = ["identifier"]


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["name", "slug"]


class ExperimentSessionSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name="api:session-detail", lookup_field="external_id")
    session_id = serializers.ReadOnlyField(source="external_id")
    team = TeamSerializer(read_only=True)
    experiment = ExperimentSerializer(read_only=True)
    participant = ParticipantSerializer(read_only=True)

    class Meta:
        model = ExperimentSession
        fields = ["url", "session_id", "team", "experiment", "participant", "created_at", "updated_at"]


class ExperimentSessionCreateSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name="api:session-detail", lookup_field="external_id")
    experiment = serializers.SlugRelatedField(slug_field="public_id", queryset=Experiment.objects)

    class Meta:
        model = ExperimentSession
        fields = ["url", "experiment"]

    def create(self, validated_data):
        request = self.context["request"]
        experiment = validated_data["experiment"]
        if experiment.team_id != request.team.id:
            raise NotFound("Experiment not found")
        participant, _created = Participant.objects.get_or_create(
            identifier=request.user.email, team=request.team, user=request.user
        )
        validated_data["team"] = request.team
        validated_data["participant"] = participant
        validated_data["llm"] = experiment.llm
        channel, _ = ExperimentChannel.objects.get_or_create(
            experiment=experiment,
            platform=ChannelPlatform.API,
            name=f"{experiment.id}-api",
        )
        validated_data["experiment_channel"] = channel
        return super().create(validated_data)
