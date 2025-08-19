import datetime

import pytest
from django.urls import reverse
from pytest_django.asserts import assertFormError, assertRedirects
from time_machine import travel

from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory
from apps.web.views import ADMIN_SLUG


@pytest.fixture()
def superuser():
    return UserFactory(is_superuser=True, is_staff=True)


@pytest.fixture()
def authed_client(client, superuser):
    client.force_login(superuser)
    return client


@pytest.mark.django_db()
def test_admin_site_redirects_to_sudo_access(superuser, authed_client):
    admin_url = reverse("admin:index")
    sudo_url = reverse("web:sudo", args=[ADMIN_SLUG])
    response = authed_client.get(admin_url)
    assert response.status_code == 302
    assert response.url == f"{sudo_url}?next={admin_url}"


@pytest.mark.django_db()
def test_escalation_renders_when_accessing_other_team(superuser, authed_client):
    other_team = TeamFactory()
    response = authed_client.get(reverse("web_team:home", args=[other_team.slug]))
    assert response.status_code == 404
    sudo_url = reverse("web:sudo", args=[other_team.slug])
    assert sudo_url in response.content.decode()


@pytest.mark.django_db()
def test_escalation_does_not_render_when_for_non_superuser(superuser, authed_client):
    superuser.is_superuser = False
    superuser.save()

    other_team = TeamFactory()
    response = authed_client.get(reverse("web_team:home", args=[other_team.slug]))
    assert response.status_code == 404
    sudo_url = reverse("web:sudo", args=[other_team.slug])
    assert sudo_url not in response.content.decode()


@pytest.mark.django_db()
def test_acquire_for_admin_site(team, superuser, authed_client):
    response = authed_client.get(reverse("web:sudo", args=[ADMIN_SLUG]))
    assert response.status_code == 200
    assert "Admin Site" in response.content.decode()
    assert superuser.email in response.content.decode()


@pytest.mark.django_db()
def test_acquire_for_invalid_team(superuser, authed_client):
    response = authed_client.get(reverse("web:sudo", args=["invalid-team"]))
    assert response.status_code == 404


@pytest.mark.django_db()
def test_acquire_for_valid_team(team, superuser, authed_client):
    response = authed_client.get(reverse("web:sudo", args=[team.slug]))
    assert response.status_code == 200
    assert team.slug in response.content.decode()


@pytest.mark.django_db()
def test_acquire_with_valid_password(superuser, authed_client):
    admin_url = reverse("admin:index")
    response = authed_client.post(
        reverse("web:sudo", args=[ADMIN_SLUG]), {"password": "password", "redirect": admin_url}
    )
    assertRedirects(response, admin_url)


@pytest.mark.django_db()
def test_acquire_with_invalid_password(superuser, authed_client):
    response = authed_client.post(
        reverse("web:sudo", args=[ADMIN_SLUG]), {"password": "wrongpassword", "redirect": "/"}
    )
    assert response.status_code == 200
    assertFormError(response.context["form"], "password", "Invalid password")


@pytest.mark.django_db()
def test_sudo_access_expires_after_30_minutes(superuser, authed_client):
    with travel(datetime.datetime.now(), tick=False) as freezer:
        admin_url = reverse("admin:index")
        # Acquire sudo access
        response = authed_client.post(
            reverse("web:sudo", args=[ADMIN_SLUG]), {"password": "password", "redirect": admin_url}
        )
        assertRedirects(response, admin_url)

        # Advance time by 29 minutes
        freezer.shift(datetime.timedelta(minutes=29))
        response = authed_client.get(admin_url)
        assert response.status_code == 200  # Should still have access

        # Advance time by 2 more minutes (31 minutes total)
        freezer.shift(datetime.timedelta(minutes=2))
        response = authed_client.get(admin_url)
        assert response.status_code == 302  # Should redirect to sudo page
