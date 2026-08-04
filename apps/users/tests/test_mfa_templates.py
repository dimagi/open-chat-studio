"""Tests for the OCS overrides of allauth's MFA templates.

OCS replaces allauth's markup wholesale, so upstream context changes are not picked up
automatically. These tests pin the context variables the overrides depend on.
"""

import pytest
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.mfa.totp.internal import auth as totp_auth
from django.urls import reverse

from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def user(db):
    return TeamWithUsersFactory.create().members.first()


@pytest.fixture()
def logged_in_client(client, user):
    """Log in for real: the MFA views require a recent authentication record in the session."""
    password = "sekr1t-passw0rd"
    user.set_password(password)
    user.save()
    response = client.post(reverse("account_login"), {"login": user.email, "password": password})
    assert response.status_code == 302, "login failed"
    return client


def test_totp_activation_shows_the_secret(logged_in_client):
    """The secret lives on the form, not on an authenticator (there isn't one yet)."""
    response = logged_in_client.get(reverse("mfa_activate_totp"))

    secret = response.context["form"].secret
    assert secret
    assert secret in response.content.decode()


@pytest.mark.parametrize("show_once", [False, True], ids=["show-always", "show-once"])
def test_recovery_codes_are_only_rendered_when_viewable(logged_in_client, user, settings, show_once):
    settings.MFA_RECOVERY_CODES_SHOW_ONCE = show_once
    totp_auth.TOTP.activate(user, totp_auth.generate_totp_secret())
    codes = RecoveryCodes.activate(user).get_unused_codes()

    first = logged_in_client.get(reverse("mfa_view_recovery_codes")).content.decode()
    second = logged_in_client.get(reverse("mfa_view_recovery_codes")).content.decode()

    assert codes[0] in first
    assert (codes[0] in second) is not show_once
    # A download link that would 403 must not be offered.
    assert (reverse("mfa_download_recovery_codes") in second) is not show_once


def test_download_link_hidden_when_no_unused_codes(logged_in_client, user, settings):
    settings.MFA_RECOVERY_CODES_SHOW_ONCE = False
    totp_auth.TOTP.activate(user, totp_auth.generate_totp_secret())
    recovery_codes = RecoveryCodes.activate(user)
    authenticator = recovery_codes.instance
    authenticator.data["used_mask"] = 2 ** len(recovery_codes.get_unused_codes()) - 1
    authenticator.save()

    response = logged_in_client.get(reverse("mfa_view_recovery_codes"))

    assert reverse("mfa_download_recovery_codes") not in response.content.decode()


def test_index_does_not_offer_recovery_codes_before_mfa_is_enabled(logged_in_client):
    response = logged_in_client.get(reverse("mfa_index"))

    assert not Authenticator.objects.exists()
    assert reverse("mfa_generate_recovery_codes") not in response.content.decode()
