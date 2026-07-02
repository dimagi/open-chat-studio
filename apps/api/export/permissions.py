from rest_framework.permissions import BasePermission


class IsTeamAdmin(BasePermission):
    def has_permission(self, request, view):
        membership = getattr(request, "team_membership", None)
        return bool(membership and membership.is_team_admin())


class TeamIsMigrating(BasePermission):
    message = "This team is not in migration mode; data export is unavailable."

    def has_permission(self, request, view):
        team = getattr(request, "team", None)
        return bool(team and team.is_migrating)
