import pytest
from django.urls import reverse

from apps.users.models import CustomUser


@pytest.mark.django_db()
class TestUiGallery:
    def test_renders_components_for_elevated_staff(self, staff_client):
        response = staff_client.get(reverse("ocs_admin:ui_gallery"))
        assert response.status_code == 200
        html = response.content.decode()
        assert "btn btn-sm btn-soft btn-primary" in html  # <c-ui.chip>
        assert "dropdown-hover" in html  # <c-ui.help>
        assert "Example of a field error." in html

    def test_hidden_from_non_staff(self, client):
        client.force_login(CustomUser.objects.create(username="member@acme.com"))
        assert client.get(reverse("ocs_admin:ui_gallery")).status_code == 404
