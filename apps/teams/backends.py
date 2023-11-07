from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import Permission

from apps.teams.utils import get_current_team


class TeamBackend(ModelBackend):
    def _get_group_permissions(self, user_obj):
        current_team = get_current_team()
        if not current_team:
            return Permission.objects.none()

        return Permission.objects.filter(group__membership__team=current_team, group__membership__user=user_obj)
