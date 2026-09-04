"""The Django admin is the fallback surface for editing flags, so it applies the same
rule as the flag admin pages: a flag decision is `everyone` or `teams`, and the
request-only waffle inputs are not editable.
"""

import pytest
from django.contrib import admin
from django.test import RequestFactory

from apps.teams.models import Flag
from apps.utils.factories.user import UserFactory

REQUEST_ONLY_FIELDS = {
    "users",
    "groups",
    "superusers",
    "staff",
    "authenticated",
    "testing",
    "rollout",
    "percent",
    "languages",
}


@pytest.mark.django_db()
def test_admin_form_offers_only_everyone_and_teams():
    """The change form built by the registered admin carries none of the excluded fields."""
    flag_admin = admin.site._registry[Flag]

    request = RequestFactory().get("/django-admin/")
    request.user = UserFactory.create(is_staff=True, is_superuser=True)

    form_class = flag_admin.get_form(request)

    form_fields = set(form_class.base_fields)
    assert REQUEST_ONLY_FIELDS.isdisjoint(form_fields)
    assert {"everyone", "teams"} <= form_fields
