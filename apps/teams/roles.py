from __future__ import annotations

from apps.users.models import CustomUser

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

ROLE_CHOICES = (
    # customize roles here
    (ROLE_ADMIN, "Administrator"),
    (ROLE_MEMBER, "Member"),
)


def is_member(user: CustomUser, team: apps.teams.models.Team) -> bool:
    if not team:
        return False
    return team.members.filter(id=user.id).exists()


def is_admin(user: CustomUser, team: apps.teams.models.Team) -> bool:
    if not team:
        return False

    from .models import Membership

    return Membership.objects.filter(team=team, user=user, role=ROLE_ADMIN).exists()
