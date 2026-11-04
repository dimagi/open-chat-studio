import pytest

from apps.service_providers.tests.oauth_authorization_code_test_utils import make_provider


@pytest.mark.django_db()
def test_verification_state_reports_reconnect_and_saved_error(team):
    provider = make_provider(team)
    provider._auth_data = {"reconnect_required": True}
    provider.extra_data = {
        "verified_credentials": True,
        "verification_error": "The provider rejected credentials.",
    }
    provider.save(update_fields=["_auth_data", "extra_data"])
    assert not provider.credentials_verified
    assert provider.verification_error == "The OAuth connection needs to be renewed; connect the provider again."
    assert provider.verification_state == "failed"

    provider._auth_data = {}
    provider.extra_data = {"verification_error": "The provider rejected credentials."}
    provider.save(update_fields=["_auth_data", "extra_data"])
    assert provider.verification_error == "The provider rejected credentials."
    assert provider.verification_state == "failed"


@pytest.mark.django_db()
def test_verification_state_distinguishes_checked_and_unchecked(team):
    provider = make_provider(team)
    provider.extra_data = {"credentials_checked_at": "2026-10-29T12:00:00+00:00"}
    provider.save(update_fields=["extra_data"])
    assert provider.credentials_checked_at.isoformat() == "2026-10-29T12:00:00+00:00"
    assert provider.verification_state == "no_model"

    provider.extra_data = {}
    provider.save(update_fields=["extra_data"])
    assert provider.credentials_checked_at is None
    assert provider.verification_state == "unchecked"
