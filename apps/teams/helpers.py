from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from apps.users.models import CustomUser
from apps.utils.slug import get_next_unique_slug

from . import roles
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
    if not request.user.is_authenticated:
        return

    team_slug = view_kwargs.get("team_slug", None)
    if team_slug:
        return get_object_or_404(Team, slug=team_slug, membership__user=request.user)

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
    team.members.add(user, through_defaults={"role": roles.ROLE_ADMIN})
    team.save()
    return team
