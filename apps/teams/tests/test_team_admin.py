import pytest
from django.contrib import admin
from django.test import RequestFactory

from apps.teams.models import Team
from apps.utils.factories.user import UserFactory


@pytest.mark.django_db()
def test_admin_form_does_not_offer_the_export_allowlist():
    """The Team change form leaves the export allowlist to the team settings form."""
    team_admin = admin.site._registry[Team]

    request = RequestFactory().get("/django-admin/")
    request.user = UserFactory.create(is_staff=True, is_superuser=True)

    form_class = team_admin.get_form(request)

    assert "exportable_experiments" not in form_class.base_fields
    assert {"name", "slug"} <= set(form_class.base_fields)
