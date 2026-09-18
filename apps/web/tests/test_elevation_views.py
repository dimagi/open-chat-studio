import datetime

import pytest
from django.urls import reverse
from pytest_django.asserts import assertFormError, assertRedirects
from time_machine import travel

from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory
from apps.web.elevation import MAX_CONCURRENT_ELEVATIONS


@pytest.fixture()
def superuser():
    return UserFactory.create(is_superuser=True, is_staff=True)


@pytest.fixture()
def authed_client(client, superuser):
    client.force_login(superuser)
    return client


@pytest.mark.django_db()
def test_admin_site_redirects_to_elevation(superuser, authed_client):
    admin_url = reverse("admin:index")
    elevate_url = reverse("web:elevate_django_admin")
    response = authed_client.get(admin_url)
    assert response.status_code == 302
    assert response.url == f"{elevate_url}?next={admin_url}"


@pytest.mark.django_db()
def test_escalation_renders_when_accessing_other_team(superuser, authed_client):
    other_team = TeamFactory.create()
    response = authed_client.get(reverse("web_team:home", args=[other_team.slug]))
    assert response.status_code == 404
    elevate_url = reverse("web:elevate_team", args=[other_team.slug])
    assert elevate_url in response.content.decode()


@pytest.mark.django_db()
def test_escalation_does_not_render_when_for_non_superuser(superuser, authed_client):
    superuser.is_superuser = False
    superuser.save()

    other_team = TeamFactory.create()
    response = authed_client.get(reverse("web_team:home", args=[other_team.slug]))
    assert response.status_code == 404
    elevate_url = reverse("web:elevate_team", args=[other_team.slug])
    assert elevate_url not in response.content.decode()


@pytest.mark.django_db()
def test_acquire_for_django_admin(team, superuser, authed_client):
    response = authed_client.get(reverse("web:elevate_django_admin"))
    assert response.status_code == 200
    assert "Django admin" in response.content.decode()
    assert superuser.email in response.content.decode()


@pytest.mark.django_db()
def test_acquire_for_invalid_team(superuser, authed_client):
    response = authed_client.get(reverse("web:elevate_team", args=["invalid-team"]))
    assert response.status_code == 404


@pytest.mark.django_db()
def test_acquire_for_valid_team(team, superuser, authed_client):
    response = authed_client.get(reverse("web:elevate_team", args=[team.slug]))
    assert response.status_code == 200
    assert team.slug in response.content.decode()


@pytest.mark.django_db()
def test_acquire_team_elevation_is_superuser_only(team, superuser, authed_client):
    """Staff may elevate into the admin surfaces, but standing in for a team member is superuser-only."""
    superuser.is_superuser = False
    superuser.save()

    assert authed_client.get(reverse("web:elevate_team", args=[team.slug])).status_code == 404
    assert authed_client.get(reverse("web:elevate_django_admin")).status_code == 200


@pytest.mark.django_db()
def test_acquire_requires_the_minimum_role(superuser, authed_client):
    superuser.is_superuser = False
    superuser.is_staff = False
    superuser.save()

    assert authed_client.get(reverse("web:elevate_django_admin")).status_code == 404
    assert authed_client.get(reverse("web:elevate_ocs_admin")).status_code == 404


@pytest.mark.django_db()
def test_acquire_with_valid_password(superuser, authed_client):
    admin_url = reverse("admin:index")
    response = authed_client.post(reverse("web:elevate_django_admin"), {"password": "password", "redirect": admin_url})
    assertRedirects(response, admin_url)


@pytest.mark.django_db()
def test_acquire_with_invalid_password(superuser, authed_client):
    response = authed_client.post(reverse("web:elevate_django_admin"), {"password": "wrongpassword", "redirect": "/"})
    assert response.status_code == 200
    assertFormError(response.context["form"], "password", "Invalid password")


@pytest.mark.django_db()
def test_release_drops_the_grant(superuser, authed_client):
    admin_url = reverse("admin:index")
    authed_client.post(reverse("web:elevate_django_admin"), {"password": "password", "redirect": admin_url})
    assert authed_client.get(admin_url).status_code == 200

    response = authed_client.get(reverse("web:release_elevation", args=["django_admin"]))

    assertRedirects(response, "/", target_status_code=302)
    assert authed_client.get(admin_url).status_code == 302


@pytest.mark.django_db()
def test_release_of_an_unknown_grant_is_a_404(superuser, authed_client):
    response = authed_client.get(reverse("web:release_elevation", args=["admin_site"]))
    assert response.status_code == 404


@pytest.mark.django_db()
def test_elevation_expires_after_30_minutes(superuser, authed_client):
    with travel(datetime.datetime.now(), tick=False) as freezer:
        admin_url = reverse("admin:index")
        response = authed_client.post(
            reverse("web:elevate_django_admin"), {"password": "password", "redirect": admin_url}
        )
        assertRedirects(response, admin_url)

        # Advance time by 29 minutes
        freezer.shift(datetime.timedelta(minutes=29))
        response = authed_client.get(admin_url)
        assert response.status_code == 200  # Should still have access

        # Advance time by 2 more minutes (31 minutes total)
        freezer.shift(datetime.timedelta(minutes=2))
        response = authed_client.get(admin_url)
        assert response.status_code == 302  # Should redirect to the elevation page


@pytest.mark.django_db()
def test_acquire_beyond_the_concurrency_cap_reports_an_error(superuser, authed_client):
    for team in TeamFactory.create_batch(MAX_CONCURRENT_ELEVATIONS):
        setup = authed_client.post(
            reverse("web:elevate_team", args=[team.slug]), {"password": "password", "redirect": "/"}
        )
        assert setup.status_code == 302

    extra = TeamFactory.create()
    response = authed_client.post(
        reverse("web:elevate_team", args=[extra.slug]), {"password": "password", "redirect": "/"}
    )

    assert response.status_code == 200
    assertFormError(
        response.context["form"],
        None,
        ["You already hold the maximum number of elevated privileges. Release one of them and try again."],
    )


@pytest.mark.django_db()
def test_banner_shows_the_grant_label_and_a_release_link(team, superuser, authed_client):
    team_home = reverse("web_team:home", args=[team.slug])
    authed_client.post(reverse("web:elevate_team", args=[team.slug]), {"password": "password", "redirect": "/"})

    content = authed_client.get(team_home, follow=True).content.decode()

    assert f"Team &quot;{team.slug}&quot;" in content
    assert reverse("web:release_elevation", args=[f"team:{team.slug}"]) in content


@pytest.mark.django_db()
def test_admin_site_shows_a_release_link(superuser, authed_client):
    admin_url = reverse("admin:index")
    authed_client.post(reverse("web:elevate_django_admin"), {"password": "password", "redirect": admin_url})

    content = authed_client.get(admin_url).content.decode()

    assert "Release Admin Access" in content
    assert reverse("web:release_elevation", args=["django_admin"]) in content
