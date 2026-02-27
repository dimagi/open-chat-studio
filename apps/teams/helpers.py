from django.http import HttpRequest
from django.utils.translation import gettext as _

from apps.users.models import CustomUser
from apps.utils.slug import get_next_unique_slug
from apps.web.superuser_utils import has_temporary_superuser_access

from .backends import make_user_team_owner
from .models import Membership, Team


def get_default_team_name_for_user(user: CustomUser):
    return (user.get_display_name().split("@")[0] or _("My Team")).title()


def get_next_unique_team_slug(team_name: str) -> str:
    """
    Gets the next unique slug based on the name. Appends -1, -2, etc. until it finds
    a unique value.
    :param team_name:
    :return:
    """
    return get_next_unique_slug(Team, team_name, "slug")


def get_team_for_request(request, view_kwargs):
    team_slug = view_kwargs.get("team_slug", None)
    if team_slug:
        return Team.objects.filter(slug=team_slug).first()

    if not request.user.is_authenticated:
        return

    return get_default_team_from_request(request)


def get_default_team_from_request(request: HttpRequest) -> Team:
    if "team" in request.session:
        try:
            return request.user.teams.get(id=request.session["team"])
        except Team.DoesNotExist:
            # user wasn't member of team from session, or it didn't exist.
            # fall back to default behavior
            del request.session["team"]
            pass
    return get_default_team_for_user(request.user)  # ty: ignore[invalid-argument-type]


def get_default_team_for_user(user: CustomUser):
    if user.teams.exists():
        return user.teams.first()
    else:
        return None


def create_default_team_for_user(user: CustomUser, team_name: str | None = None):
    team_name = team_name or get_default_team_name_for_user(user)
    slug = get_next_unique_team_slug(team_name)
    team = Team.objects.create(name=team_name, slug=slug)
    make_user_team_owner(team, user)
    return team


def get_team_membership_for_request(request: HttpRequest):
    if request.user.is_authenticated and request.team:
        membership = Membership.objects.filter(team=request.team, user=request.user).first()
        if not membership and request.user.is_superuser and has_temporary_superuser_access(request, request.team.slug):
            membership = SuperuserMembership(request.user, request.team)  # ty: ignore[invalid-argument-type]
        return membership


class SuperuserMembership:
    """Dummy membership for superusers who have temporary access to a team."""

    def __init__(self, user: CustomUser, team: Team):
        self.user = user
        self.team = team

    def is_team_admin(self):
        return self.user.is_superuser
