from rest_framework import serializers


class FilterSetCreateUpdateSerializer(serializers.Serializer):
    """
    Serializer for creating and updating FilterSet objects.
    Validates the fields that can be set by users.
    """

    name = serializers.CharField(max_length=256, required=False, allow_blank=False)
    filter_params = serializers.JSONField(required=False)
    is_shared = serializers.BooleanField(required=False)
    is_starred = serializers.BooleanField(required=False)
    is_default_for_user = serializers.BooleanField(required=False)
    is_default_for_team = serializers.BooleanField(required=False)

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
