"""The manifest read endpoint the sync command consumes: the model call order plus per-model
config the sync needs. It also serves as the allowlist of models the sync is permitted to read."""

from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.teams.sync.manifest import build_manifest


class IsTeamAdmin(BasePermission):
    def has_permission(self, request, view):
        membership = getattr(request, "team_membership", None)
        return bool(membership and membership.is_team_admin())


class ManifestView(APIView):
    permission_classes = [IsAuthenticated, IsTeamAdmin]

    def get(self, request):
        return Response(build_manifest())
