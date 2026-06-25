from rest_framework.permissions import BasePermission


class IsTeamAdmin(BasePermission):
    def has_permission(self, request, view):
        membership = getattr(request, "team_membership", None)
        return bool(membership and membership.is_team_admin())
