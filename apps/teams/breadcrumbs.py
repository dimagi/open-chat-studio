from django.urls import reverse
from django.utils.translation import gettext

from apps.generics.breadcrumbs import Crumb


def team_settings_crumb(team_slug: str) -> Crumb:
    return gettext("Team Settings"), reverse("single_team:manage_team", args=[team_slug])
