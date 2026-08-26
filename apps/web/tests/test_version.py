from io import StringIO

import pytest
from django.core.management import call_command, get_commands
from django.test import override_settings
from django.urls import reverse

from apps.users.models import CustomUser


@pytest.mark.parametrize(
    "version",
    [
        pytest.param("v1.2.0", id="tagged-release"),
        pytest.param("v1.2.0-37-gabc1234", id="build-off-main"),
        pytest.param("unknown", id="built-outside-ci"),
    ],
)
def test_ocs_version_command_prints_running_version(version):
    out = StringIO()
    with override_settings(OCS_VERSION=version):
        call_command("ocs_version", stdout=out)
    assert out.getvalue().strip() == version


def test_command_is_not_named_version():
    """`manage.py version` is unreachable: Django's ManagementUtility intercepts that
    subcommand and prints its own version before any command lookup happens
    (django/core/management/__init__.py). call_command() bypasses that, so only an
    end-to-end check catches the collision."""
    commands = get_commands()
    assert "ocs_version" in commands
    assert commands.get("version") is None


@pytest.mark.django_db()
def test_admin_home_shows_version_to_staff(client):
    client.force_login(CustomUser.objects.create(username="staff@acme.com", is_staff=True))
    with override_settings(OCS_VERSION="v1.2.0"):
        response = client.get(reverse("ocs_admin:home"))
    assert response.status_code == 200
    assert "v1.2.0" in response.content.decode()


@pytest.mark.django_db()
def test_version_not_exposed_to_non_staff(client):
    """The version tells an attacker which CVEs apply, so it stays behind the staff gate."""
    client.force_login(CustomUser.objects.create(username="user@acme.com"))
    with override_settings(OCS_VERSION="v1.2.0"):
        response = client.get(reverse("ocs_admin:home"))
    assert response.status_code != 200
