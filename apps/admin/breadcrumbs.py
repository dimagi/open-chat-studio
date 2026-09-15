from django.urls import reverse
from django.utils.translation import gettext

from apps.generics.breadcrumbs import Crumb


def admin_crumb() -> Crumb:
    return gettext("Admin"), reverse("ocs_admin:home")
