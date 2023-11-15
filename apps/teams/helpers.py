from django.http import HttpRequest
from django.utils.translation import gettext as _

from apps.users.models import CustomUser
from apps.utils.slug import get_next_unique_slug

from .backends import make_user_team_owner
from .models import Team


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
    return get_default_team_for_user(request.user)


def get_default_team_for_user(user: CustomUser):
    if user.teams.exists():
        return user.teams.first()
    else:
        return None


def create_default_team_for_user(user: CustomUser, team_name: str = None):
    team_name = team_name or get_default_team_name_for_user(user)
    slug = get_next_unique_team_slug(team_name)
    team = Team.objects.create(name=team_name, slug=slug)
    make_user_team_owner(team, user)
    return team
