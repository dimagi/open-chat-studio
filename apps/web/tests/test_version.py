import re
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command, execute_from_command_line, get_commands
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
    with override_settings(OCS_BUILD_VERSION=version):
        call_command("ocs_version", stdout=out)
    assert out.getvalue().strip() == version


def test_command_is_reachable_through_manage_py(capsys):
    """Covers the dispatch path, not just registration. `manage.py version` is
    unreachable because Django's ManagementUtility intercepts that subcommand and
    prints its own version before any command lookup happens
    (django/core/management/__init__.py). call_command() bypasses that interception, so
    it reported success while the real CLI printed Django's version instead."""
    with override_settings(OCS_BUILD_VERSION="v1.2.0"):
        execute_from_command_line(["manage.py", "ocs_version"])
    assert capsys.readouterr().out.strip() == "v1.2.0"


def test_command_is_not_named_version():
    commands = get_commands()
    assert "ocs_version" in commands
    assert commands.get("version") is None


@pytest.mark.django_db()
def test_admin_home_shows_version_to_staff(client):
    client.force_login(CustomUser.objects.create(username="staff@acme.com", is_staff=True))
    with override_settings(OCS_BUILD_VERSION="v1.2.0"):
        response = client.get(reverse("ocs_admin:home"))
    assert response.status_code == 200
    assert "v1.2.0" in response.content.decode()


@pytest.mark.django_db()
def test_version_not_exposed_to_non_staff(client):
    """The version tells an attacker which CVEs apply, so it stays behind the staff gate."""
    client.force_login(CustomUser.objects.create(username="user@acme.com"))
    with override_settings(OCS_BUILD_VERSION="v1.2.0"):
        response = client.get(reverse("ocs_admin:home"))
    assert response.status_code == 302
    assert "v1.2.0" not in response.content.decode()


@pytest.mark.django_db()
def test_version_not_exposed_to_anonymous(client):
    with override_settings(OCS_BUILD_VERSION="v1.2.0"):
        response = client.get(reverse("ocs_admin:home"))
    assert response.status_code == 302
    assert "v1.2.0" not in response.content.decode()


def test_build_version_env_var_does_not_collide_with_compose():
    """`docker-compose.prod.yml` passes `.env` as an env_file to every app service, and
    Compose puts env_file above the image's ENV. So any variable it interpolates from
    `.env` would silently overwrite the value baked in at build time. Guards the rename
    that fixed exactly that: the build version must not reuse a Compose-facing name."""
    compose = (Path(settings.BASE_DIR) / "docker-compose.prod.yml").read_text()
    interpolated = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", compose))
    assert "OCS_BUILD_VERSION" not in interpolated, (
        f"docker-compose.prod.yml interpolates OCS_BUILD_VERSION, which would override "
        f"the value baked into the image. Compose-facing names: {sorted(interpolated)}"
    )
