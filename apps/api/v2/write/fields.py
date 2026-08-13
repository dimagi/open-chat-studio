"""Serializer fields shared by the v2 write endpoints."""

from rest_framework import serializers


class TeamScopedRelatedField(serializers.PrimaryKeyRelatedField):
    """A reference restricted to the request team's rows.

    The queryset is resolved at validation time rather than at construction: a nested serializer's
    fields are built before they are bound to a parent, so ``__init__`` cannot reach the request.
    ``RelatedField`` skips its "must provide a queryset" assertion when ``get_queryset`` is
    overridden.

    A cross-team id simply misses the queryset, exactly as a nonexistent id does, so both surface
    as a 400 on the field. Telling them apart would need a second, global query whose only effect
    is to leak whether the id exists in another team.
    """

    def __init__(self, get_team_queryset, **kwargs):
        self.get_team_queryset = get_team_queryset
        super().__init__(**kwargs)

    def get_queryset(self):
        return self.get_team_queryset(self.context["request"].team)
