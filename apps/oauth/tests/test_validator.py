from types import SimpleNamespace

import pytest
from allauth.account.models import EmailAddress

from apps.oauth.validator import APIScopedValidator
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def validator():
    return APIScopedValidator()


@pytest.mark.django_db()
def test_email_verified_claim_true_when_email_confirmed(validator):
    """email_verified should be True when the user's email address has been confirmed."""
    user = UserFactory.create()
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    request = SimpleNamespace(user=user)

    claims = validator.get_additional_claims(request)

    assert claims["sub"] == user.email
    assert claims["email_verified"] is True


@pytest.mark.django_db()
def test_email_verified_claim_false_when_email_unconfirmed(validator):
    """email_verified should be False when there is an unverified EmailAddress."""
    user = UserFactory.create()
    EmailAddress.objects.create(user=user, email=user.email, verified=False, primary=True)
    request = SimpleNamespace(user=user)

    claims = validator.get_additional_claims(request)

    assert claims["email_verified"] is False


@pytest.mark.django_db()
def test_email_verified_claim_false_when_no_email_address_record(validator):
    """email_verified should be False when there is no EmailAddress record at all."""
    user = UserFactory.create()
    request = SimpleNamespace(user=user)

    claims = validator.get_additional_claims(request)

    assert claims["email_verified"] is False


@pytest.mark.django_db()
def test_email_verified_claim_is_scoped_to_openid():
    """The email_verified claim is only emitted within the openid scope."""
    assert APIScopedValidator.oidc_claim_scope["email_verified"] == "openid"
