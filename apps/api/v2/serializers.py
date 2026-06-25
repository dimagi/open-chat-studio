from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.api.serializers import TeamSerializer
from apps.users.helpers import user_has_confirmed_email_address
from apps.users.models import CustomUser


class MeSerializer(serializers.ModelSerializer):
    team = serializers.SerializerMethodField()
    email_verified = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = ["id", "username", "email", "first_name", "last_name", "email_verified", "team"]

    @extend_schema_field(TeamSerializer)
    def get_team(self, obj):
        team = self.context.get("team")
        return TeamSerializer(team).data if team else None

    @extend_schema_field(serializers.BooleanField())
    def get_email_verified(self, obj):
        return user_has_confirmed_email_address(obj, obj.email)
