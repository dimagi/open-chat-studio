import datetime
from urllib.parse import quote

import pytest
from django.urls import reverse, reverse_lazy
from pytest_django.asserts import assertRedirects
from time_machine import travel

from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.user import UserFactory
from apps.web.elevation import MAX_CONCURRENT_ELEVATIONS, STASH_MAX_AGE, TOO_MANY_ELEVATIONS_MESSAGE

REAUTH_URL = str(reverse_lazy("account_reauthenticate"))


@pytest.fixture()
def superuser():
    return UserFactory.create(is_superuser=True, is_staff=True)


@pytest.fixture()
def authed_client(client, superuser):
    client.force_login(superuser)
    return client


def elevate(client, acquire_url, next_url="/", password="password"):
    """Walk the acquire → re-authenticate → resume round trip, returning the final response."""
    started = client.get(acquire_url, {"next": next_url})
    assert started.status_code == 302, started
    assert started.url == REAUTH_URL
    return client.post(REAUTH_URL, {"password": password})


@pytest.mark.django_db()
def test_admin_site_redirects_to_elevation(superuser, authed_client):
    admin_url = reverse("admin:index")
    elevate_url = reverse("web:elevate_django_admin")
    response = authed_client.get(admin_url)
    assert response.status_code == 302
    assert response.url == f"{elevate_url}?next={quote(admin_url, safe='')}"


@pytest.mark.django_db()
def test_admin_site_steps_up_staff_as_well_as_superusers(client):
    """`AdminSite.has_permission` is `is_staff`, so staff have to prove themselves too."""
    staff = UserFactory.create(is_staff=True)
    client.force_login(staff)
    admin_url = reverse("admin:index")

    response = client.get(admin_url)

    assert response.status_code == 302
    assert response.url == f"{reverse('web:elevate_django_admin')}?next={quote(admin_url, safe='')}"

    elevate(client, reverse("web:elevate_django_admin"), admin_url)
    assert client.get(admin_url).status_code == 200


@pytest.mark.django_db()
def test_ocs_admin_is_reached_through_its_own_elevation(superuser, authed_client):
    """The Django admin grant is a different grant, so it does not open /admin/."""
    ocs_admin_url = reverse("ocs_admin:home")
    elevate(authed_client, reverse("web:elevate_django_admin"))
    assert authed_client.get(ocs_admin_url).status_code == 302

    elevate(authed_client, reverse("web:elevate_ocs_admin"), ocs_admin_url)

    assert authed_client.get(ocs_admin_url).status_code == 200


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
def test_acquire_hands_off_to_reauthentication(superuser, authed_client):
    response = authed_client.get(reverse("web:elevate_django_admin"))
    assertRedirects(response, REAUTH_URL)
    assert superuser.email in authed_client.get(REAUTH_URL).content.decode()


@pytest.mark.django_db()
def test_the_prompt_names_the_grant_being_requested(team, superuser, authed_client):
    """The prompt has to name the grant, so the user can see what they are confirming."""
    authed_client.get(reverse("web:elevate_team", args=[team.slug]))

    content = authed_client.get(REAUTH_URL).content.decode()

    assert f"Team &quot;{team.slug}&quot;" in content


@pytest.mark.django_db()
def test_the_prompt_names_no_grant_outside_an_elevation(superuser, authed_client):
    content = authed_client.get(REAUTH_URL).content.decode()

    assert "You are requesting elevated access" not in content


@pytest.mark.django_db()
def test_the_prompt_names_no_grant_once_the_request_has_gone_stale(team, superuser, authed_client):
    """`complete_elevation` would refuse it, so the prompt must not offer it."""
    with travel(datetime.datetime.now(), tick=False) as freezer:
        authed_client.get(reverse("web:elevate_team", args=[team.slug]))

        freezer.shift(datetime.timedelta(seconds=STASH_MAX_AGE + 1))
        content = authed_client.get(REAUTH_URL).content.decode()

    assert f"Team &quot;{team.slug}&quot;" not in content


@pytest.mark.django_db()
def test_acquire_for_invalid_team(superuser, authed_client):
    response = authed_client.get(reverse("web:elevate_team", args=["invalid-team"]))
    assert response.status_code == 404


@pytest.mark.django_db()
def test_acquire_for_a_team_the_user_belongs_to_skips_the_prompt(team, superuser, authed_client):
    MembershipFactory.create(team=team, user=superuser)
    team_home = reverse("web_team:home", args=[team.slug])

    response = authed_client.get(reverse("web:elevate_team", args=[team.slug]), {"next": team_home})

    assertRedirects(response, team_home, target_status_code=302)


@pytest.mark.django_db()
def test_acquire_while_already_elevated_skips_the_prompt(superuser, authed_client):
    admin_url = reverse("admin:index")
    elevate(authed_client, reverse("web:elevate_django_admin"), admin_url)

    response = authed_client.get(reverse("web:elevate_django_admin"), {"next": admin_url})

    assertRedirects(response, admin_url)


@pytest.mark.django_db()
def test_acquire_team_elevation_is_superuser_only(team, superuser, authed_client):
    """Staff may elevate into the admin surfaces, but standing in for a team member is superuser-only."""
    superuser.is_superuser = False
    superuser.save()

    assert authed_client.get(reverse("web:elevate_team", args=[team.slug])).status_code == 404
    assertRedirects(authed_client.get(reverse("web:elevate_django_admin")), REAUTH_URL)


@pytest.mark.django_db()
def test_acquire_requires_the_minimum_role(superuser, authed_client):
    superuser.is_superuser = False
    superuser.is_staff = False
    superuser.save()

    assert authed_client.get(reverse("web:elevate_django_admin")).status_code == 404
    assert authed_client.get(reverse("web:elevate_ocs_admin")).status_code == 404


@pytest.mark.django_db()
def test_acquire_after_reauthentication(superuser, authed_client):
    admin_url = reverse("admin:index")
    response = elevate(authed_client, reverse("web:elevate_django_admin"), admin_url)
    assertRedirects(response, admin_url)


@pytest.mark.django_db()
def test_acquire_with_invalid_password_does_not_elevate(superuser, authed_client):
    admin_url = reverse("admin:index")
    response = elevate(authed_client, reverse("web:elevate_django_admin"), admin_url, password="wrongpassword")

    assert response.status_code == 200
    assert authed_client.get(admin_url).status_code == 302


@pytest.mark.django_db()
def test_reauthentication_is_refused_when_the_user_has_no_method(client):
    """An SSO-only account has neither a usable password nor MFA, so there is nothing to prove with."""
    sso_user = UserFactory.create(is_staff=True)
    sso_user.set_unusable_password()
    sso_user.save()
    client.force_login(sso_user)

    started = client.get(reverse("web:elevate_django_admin"))

    assertRedirects(started, REAUTH_URL, target_status_code=403)


@pytest.mark.django_db()
def test_a_stale_stash_is_not_completed(superuser, authed_client):
    """An abandoned elevation must not be granted by an unrelated re-authentication later on."""
    admin_url = reverse("admin:index")
    with travel(datetime.datetime.now(), tick=False) as freezer:
        authed_client.get(reverse("web:elevate_django_admin"), {"next": admin_url})

        freezer.shift(datetime.timedelta(seconds=STASH_MAX_AGE + 1))
        response = authed_client.post(REAUTH_URL, {"password": "password"})

    assertRedirects(response, "/", target_status_code=302)
    assert authed_client.get(admin_url).status_code == 302


@pytest.mark.django_db()
def test_elevation_is_refused_when_the_role_is_lost_before_the_proof(superuser, authed_client):
    authed_client.get(reverse("web:elevate_django_admin"))

    superuser.is_superuser = False
    superuser.is_staff = False
    superuser.save()

    response = authed_client.post(REAUTH_URL, {"password": "password"})

    assertRedirects(response, "/", target_status_code=302)


@pytest.mark.django_db()
def test_release_drops_the_grant(superuser, authed_client):
    admin_url = reverse("admin:index")
    elevate(authed_client, reverse("web:elevate_django_admin"), admin_url)
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
        response = elevate(authed_client, reverse("web:elevate_django_admin"), admin_url)
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
        setup = elevate(authed_client, reverse("web:elevate_team", args=[team.slug]))
        assert setup.status_code == 302

    extra = TeamFactory.create()
    response = authed_client.get(reverse("web:elevate_team", args=[extra.slug]), follow=True)

    assert TOO_MANY_ELEVATIONS_MESSAGE in [str(message) for message in response.context["messages"]]


@pytest.mark.django_db()
def test_banner_shows_the_grant_label_and_a_release_link(team, superuser, authed_client):
    team_home = reverse("web_team:home", args=[team.slug])
    elevate(authed_client, reverse("web:elevate_team", args=[team.slug]))

    content = authed_client.get(team_home, follow=True).content.decode()

    assert f"Team &quot;{team.slug}&quot;" in content
    assert reverse("web:release_elevation", args=[f"team:{team.slug}"]) in content


@pytest.mark.django_db()
def test_admin_site_shows_a_release_link(superuser, authed_client):
    admin_url = reverse("admin:index")
    elevate(authed_client, reverse("web:elevate_django_admin"), admin_url)

    content = authed_client.get(admin_url).content.decode()

    assert "Release Admin Access" in content
    assert reverse("web:release_elevation", args=["django_admin"]) in content
