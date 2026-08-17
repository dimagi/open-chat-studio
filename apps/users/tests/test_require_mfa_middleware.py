"""Tests for the staff MFA requirement (apps.users.middleware.RequireMfaForStaffMiddleware).

``REQUIRE_MFA_FOR_STAFF`` defaults off in development and under test (see config/settings.py), so
every test here switches it on explicitly.
"""

import time

import pytest
from allauth.account.models import EmailAddress
from allauth.mfa.totp.internal import auth as totp_auth
from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.urls import reverse

from apps.users.middleware import RequireMfaForStaffMiddleware
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

PASSWORD = "sekr1t-passw0rd"


@pytest.fixture(autouse=True)
def _require_mfa(settings):
    settings.REQUIRE_MFA_FOR_STAFF = True


@pytest.fixture()
def user(db):
    user = TeamWithUsersFactory.create().members.first()
    user.set_password(PASSWORD)
    user.save()
    return user


@pytest.fixture()
def gated_page():
    """A page with no bearing on MFA, which a gated user must not reach."""
    return reverse("users:user_profile")


def _login(client, user):
    """Log in for real: the MFA views require a recent authentication record in the session."""
    response = client.post(reverse("account_login"), {"login": user.email, "password": PASSWORD})
    assert response.status_code == 302, "login failed"
    return client


@pytest.mark.parametrize(
    ("is_staff", "is_superuser"),
    [
        pytest.param(True, False, id="staff"),
        pytest.param(False, True, id="superuser"),
        pytest.param(True, True, id="staff-and-superuser"),
    ],
)
def test_privileged_user_without_mfa_is_sent_to_setup(client, user, gated_page, is_staff, is_superuser):
    user.is_staff = is_staff
    user.is_superuser = is_superuser
    user.save()
    client.force_login(user)

    response = client.get(gated_page)

    assert response.status_code == 302
    assert response["Location"] == reverse("mfa_activate_totp")


def test_redirect_explains_itself(client, user, gated_page):
    user.is_superuser = True
    user.save()
    client.force_login(user)

    response = client.get(gated_page, follow=True)

    assert [str(message) for message in response.context["messages"]] == [
        "Two-factor authentication is required for staff accounts. Please set it up to continue."
    ]


def test_htmx_requests_get_a_client_redirect(client, user, gated_page):
    """A 302 would be swapped into the page fragment; the browser has to navigate instead."""
    user.is_superuser = True
    user.save()
    client.force_login(user)

    response = client.get(gated_page, headers={"hx-request": "true"})

    assert response.status_code == 200
    assert response["HX-Redirect"] == reverse("mfa_activate_totp")


def test_background_htmx_request_from_the_setup_page_is_dropped_not_redirected(client, user):
    """The reload loop: every page fires the banner poll, so an HX-Redirect here never settles.

    htmx would navigate the browser to the setup page, whose own banner poll would be redirected
    again -- the page reloads forever. 204 also keeps a warning per page load out of the log.
    """
    user.is_superuser = True
    user.save()
    client.force_login(user)

    response = client.get(
        reverse("banners:load_banners"),
        headers={"hx-request": "true", "hx-current-url": f"http://testserver{reverse('mfa_activate_totp')}"},
    )

    assert response.status_code == 204
    assert "HX-Redirect" not in response.headers


def test_browser_reload_stream_is_not_gated(client, user):
    """A redirected event stream makes the dev server's reload worker reload the page in a loop."""
    middleware = RequireMfaForStaffMiddleware(lambda request: None)

    assert middleware._is_exempt_path("/__reload__/events/")
    assert not middleware._is_exempt_path(reverse("users:user_profile"))


def test_unprivileged_user_without_mfa_is_not_gated(client, user, gated_page):
    client.force_login(user)

    assert client.get(gated_page).status_code == 200


def test_privileged_user_with_mfa_is_not_gated(client, user, gated_page):
    user.is_superuser = True
    user.save()
    totp_auth.TOTP.activate(user, totp_auth.generate_totp_secret())
    client.force_login(user)

    assert client.get(gated_page).status_code == 200


def test_requirement_can_be_switched_off(client, user, gated_page, settings):
    settings.REQUIRE_MFA_FOR_STAFF = False
    user.is_superuser = True
    user.save()
    client.force_login(user)

    assert client.get(gated_page).status_code == 200


def test_anonymous_users_are_not_gated(client, gated_page):
    response = client.get(gated_page)

    assert reverse("mfa_activate_totp") not in response["Location"]


@pytest.mark.parametrize(
    "url_name",
    [
        pytest.param("mfa_index", id="mfa-overview"),
        pytest.param("mfa_activate_totp", id="mfa-setup"),
        pytest.param("account_email", id="email-addresses"),
        pytest.param("account_change_password", id="change-password"),
    ],
)
def test_enrolment_and_account_pages_stay_reachable(client, user, url_name):
    user.is_superuser = True
    user.save()
    _login(client, user)

    assert client.get(reverse(url_name)).status_code == 200


def test_logout_stays_reachable(client, user):
    user.is_superuser = True
    user.save()
    client.force_login(user)

    response = client.get(reverse("account_logout"))

    assert response["Location"] != reverse("mfa_activate_totp")
    assert not client.session.get("_auth_user_id")


def test_sso_front_channel_logout_stays_reachable(client, user):
    """The IdP calls this with the user's cookies; a redirect would leave the session alive."""
    user.is_superuser = True
    user.save()
    client.force_login(user)

    response = client.get(reverse("sso:logout"))

    assert response.status_code == 200


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("/accounts/microsoft/login/", id="provider-login"),
        pytest.param("/accounts/microsoft/login/callback/", id="provider-callback"),
    ],
)
def test_sso_login_round_trip_is_not_gated(client, user, url):
    """Gating the provider round trip leaves an SSO login unable to complete."""
    app = SocialApp.objects.create(provider="microsoft", name="Microsoft", client_id="id", secret="secret")
    app.sites.add(Site.objects.get_current())
    user.is_superuser = True
    user.set_unusable_password()
    user.save()
    client.force_login(user)

    response = client.get(url)

    assert response.get("Location") != reverse("mfa_activate_totp")


@pytest.mark.parametrize(
    ("has_password", "email_state"),
    [
        pytest.param(True, "verified", id="password-verified-email"),
        pytest.param(True, "unverified", id="password-unverified-email"),
        pytest.param(True, "none", id="password-no-email-row"),
        pytest.param(False, "verified", id="sso-only-verified-email"),
        pytest.param(False, "unverified", id="sso-only-unverified-email"),
        pytest.param(False, "none", id="sso-only-no-email-row"),
    ],
)
def test_gated_user_always_lands_somewhere(client, user, gated_page, has_password, email_state):
    """No account state may leave the browser bouncing between the gate and the setup flow.

    ``/accounts/login/`` sends an authenticated user to ``LOGIN_REDIRECT_URL``, which the gate
    redirects back to the setup page -- so a gated URL anywhere in the enrolment flow loops.
    """
    user.is_superuser = True
    if not has_password:
        user.set_unusable_password()
    user.save()
    if email_state != "none":
        EmailAddress.objects.create(user=user, email=user.email, verified=email_state == "verified", primary=True)
    client.force_login(user)

    url = gated_page
    visited = []
    for _ in range(10):
        response = client.get(url)
        visited.append(url)
        if response.status_code != 302:
            break
        url = response["Location"]
    else:
        pytest.fail(f"redirect loop: {visited}")

    assert response.status_code == 200


def test_unverified_email_is_sent_to_verify_it_first(client, user, gated_page):
    """allauth refuses to enable MFA with an unverified address, so the setup page is a dead end."""
    user.is_superuser = True
    user.save()
    EmailAddress.objects.create(user=user, email=user.email, verified=False, primary=True)
    _login(client, user)

    response = client.get(gated_page)

    assert response["Location"] == reverse("account_email")
    # Pin the allauth behaviour this branch exists for: the setup page refuses to serve the form.
    assert client.get(reverse("mfa_activate_totp"))["Location"] == reverse("mfa_index")


def test_returning_user_can_reauthenticate_and_enrol(client, user, gated_page):
    """A session with no recent authentication has to pass through allauth's reauthentication."""
    user.is_superuser = True
    user.save()
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    _login(client, user)
    session = client.session
    session["account_authentication_methods"] = [{"method": "password", "at": time.time() - 100_000}]
    session.save()

    setup = client.get(gated_page, follow=True)
    assert setup.redirect_chain[-1][0].startswith(reverse("account_reauthenticate"))

    resumed = client.post(
        setup.redirect_chain[-1][0],
        {"password": PASSWORD},
        follow=True,
    )

    assert resumed.status_code == 200
    assert resumed.redirect_chain[-1][0] == reverse("mfa_activate_totp")


@pytest.mark.parametrize(
    ("method", "url_name"),
    [
        pytest.param("get", "api:experiment-list", id="experiments"),
        # /api/chat/ enables DRF's SessionAuthentication, so a staff session really does authenticate
        # here -- exempting the prefix would have been a way around the requirement.
        pytest.param("post", "api:chat:start-session", id="chat"),
    ],
)
def test_session_authenticated_api_requests_are_refused(client, user, method, url_name):
    """Gated, but with a 403: an API caller can't act on a redirect to an HTML setup page."""
    user.is_superuser = True
    user.save()
    client.force_login(user)

    response = getattr(client, method)(reverse(url_name))

    assert response.status_code == 403


def test_api_key_requests_are_unaffected(db, user):
    """The gate reads the session; DRF authenticates keys inside the view, long after it runs.

    So a staff user's integrations keep working while they enrol -- only their browser is gated.
    """
    user.is_superuser = True
    user.save()
    api_client = ApiTestClient(user, user.teams.first())

    response = api_client.get(reverse("api:experiment-list"))

    assert response.status_code == 200
