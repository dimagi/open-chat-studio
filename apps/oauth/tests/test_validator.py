from types import SimpleNamespace

import pytest
from allauth.account.models import EmailAddress

from apps.oauth.validator import APIScopedValidator
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def validator():
    return APIScopedValidator()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("verified", "expected"),
    [
        pytest.param(True, True, id="confirmed-email"),
        pytest.param(False, False, id="unconfirmed-email"),
        pytest.param(None, False, id="no-email-address-record"),
    ],
)
def test_email_verified_claim_reflects_email_confirmation(validator, verified, expected):
    """email_verified mirrors whether the user's primary email address is confirmed.

    ``verified`` is the state of the EmailAddress record to create, or ``None`` to
    create no record at all.
    """
    user = UserFactory.create()
    if verified is not None:
        EmailAddress.objects.create(user=user, email=user.email, verified=verified, primary=True)
    request = SimpleNamespace(user=user)

    claims = validator.get_additional_claims(request)

    assert claims["sub"] == user.email
    assert claims["email_verified"] is expected


@pytest.mark.django_db()
def test_email_verified_claim_is_scoped_to_openid():
    """The email_verified claim is only emitted within the openid scope."""
    assert APIScopedValidator.oidc_claim_scope["email_verified"] == "openid"
