from rest_framework import serializers

from apps.experiments.models import ExperimentSession, Participant
from apps.teams.models import Team


class ExperimentSerializer(serializers.Serializer):
    url = serializers.HyperlinkedIdentityField(view_name="api:experiment-detail", lookup_field="public_id")
    name = serializers.CharField()
    experiment_id = serializers.UUIDField(source="public_id")


class ParticipantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Participant
        fields = ["identifier"]


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["name", "slug"]


class ExperimentSessionSerializer(serializers.ModelSerializer):
    session_id = serializers.ReadOnlyField(source="external_id")
    team = TeamSerializer(read_only=True)
    experiment = ExperimentSerializer(read_only=True)
    participant = ParticipantSerializer(read_only=True)

    class Meta:
        model = ExperimentSession
        fields = ["session_id", "team", "experiment", "participant", "created_at", "updated_at"]
