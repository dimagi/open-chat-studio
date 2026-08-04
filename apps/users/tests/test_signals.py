import pytest
from allauth.mfa.models import Authenticator
from allauth.mfa.signals import authentication_failed

from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.django_db()
@pytest.mark.parametrize("reauthentication", [False, True], ids=["login", "reauthentication"])
def test_failed_mfa_challenge_is_logged(rf, caplog, reauthentication):
    user = TeamWithUsersFactory.create().members.first()
    authenticator = Authenticator.objects.create(user=user, type=Authenticator.Type.TOTP, data={})
    request = rf.post("/accounts/2fa/authenticate/", REMOTE_ADDR="198.51.100.7")

    with caplog.at_level("WARNING", logger="ocs.users"):
        authentication_failed.send(
            sender=Authenticator,
            request=request,
            user=user,
            authenticator=authenticator,
            reauthentication=reauthentication,
        )

    record = next(r for r in caplog.records if r.message == "mfa.authentication_failed")
    assert record.user_id == user.pk
    assert record.authenticator_type == Authenticator.Type.TOTP
    assert record.reauthentication is reauthentication
    assert record.client_ip == "198.51.100.7"
