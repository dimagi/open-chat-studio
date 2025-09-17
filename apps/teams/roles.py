import apps.teams.models
from apps.teams.backends import SUPER_ADMIN_GROUP
from apps.users.models import CustomUser


def is_member(user: CustomUser, team: apps.teams.models.Team) -> bool:
    if not team:
        return False
    return team.members.filter(id=user.id).exists()


def is_super_admin(user: CustomUser, team: apps.teams.models.Team) -> bool:
    if not user or not user.is_authenticated or not team:
        return False
    membership = user.membership_set.get(team=team)
    return membership.groups.filter(name=SUPER_ADMIN_GROUP).exists()
