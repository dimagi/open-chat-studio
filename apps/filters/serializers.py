from rest_framework import serializers

from apps.filters.models import FilterSet


class FilterSetSerializer(serializers.ModelSerializer):
    """
    Serializer for creating and updating FilterSet objects.
    Validates the fields that can be set by users.
    """

    is_user_filter = serializers.SerializerMethodField("get_is_user_filter")

    class Meta:
        model = FilterSet
        fields = [
            "id",
            "name",
            "table_type",
            "filter_query_string",
            "is_shared",
            "is_starred",
            "is_default_for_user",
            "is_default_for_team",
            "is_user_filter",
        ]

    def get_is_user_filter(self, obj):
        return obj.user == self.context.get("request_user")

    def validate_name(self, value):
        """Ensure name is not empty after stripping whitespace."""
        if value is not None:
            stripped = value.strip()
            if not stripped:
                raise serializers.ValidationError("Name cannot be empty or just whitespace.")
            return stripped
        return value

    def validate_is_shared(self, value):
        """Only team admins can share filter sets."""
        if value is True:
            is_team_admin = self.context.get("is_team_admin", False)
            if not is_team_admin:
                raise serializers.ValidationError("Only team admins can share filter sets.")
        return value

    def validate_is_default_for_team(self, value):
        """Only team admins can set team defaults."""
        if value is True:
            is_team_admin = self.context.get("is_team_admin", False)
            if not is_team_admin:
                raise serializers.ValidationError("Only team admins can set team default filter sets.")
        return value
