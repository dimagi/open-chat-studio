from datetime import timedelta

import pytest
import time_machine
from django.core import signing
from django.utils import timezone

from apps.api.session_tokens import (
    SESSION_TOKEN_SALT,
    issue_session_token,
    parse_session_token,
    session_token_expired,
    validate_session_token,
)
from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_token_round_trip():
    session = ExperimentSessionFactory.create()
    token = issue_session_token(session)
    assert validate_session_token(token, session.external_id) is True


@pytest.mark.django_db()
def test_tampered_token_rejected():
    session = ExperimentSessionFactory.create()
    token = issue_session_token(session)
    assert validate_session_token(token[:-2] + "xx", session.external_id) is False


def test_garbage_token_rejected():
    assert validate_session_token("not-a-token", "some-id") is False


def test_none_token_rejected():
    assert validate_session_token(None, "some-id") is False


def test_empty_token_rejected():
    assert validate_session_token("", "some-id") is False


@pytest.mark.django_db()
def test_token_for_other_session_rejected():
    session = ExperimentSessionFactory.create()
    other = ExperimentSessionFactory.create(experiment=session.experiment)
    token = issue_session_token(other)
    assert validate_session_token(token, session.external_id) is False


@pytest.mark.django_db()
def test_wrong_salt_rejected():
    """A value signed elsewhere in the app with a different salt must not validate."""
    session = ExperimentSessionFactory.create()
    forged = signing.dumps({"sid": str(session.external_id)}, salt="other-salt")
    assert validate_session_token(forged, session.external_id) is False
    # sanity: the real salt is what issue_session_token uses
    assert SESSION_TOKEN_SALT == "ocs.chat.session-token"


@pytest.mark.django_db()
def test_dormant_session_within_lifetime_not_expired():
    """Dormancy alone no longer expires anything — only age does."""
    session = ExperimentSessionFactory.create()
    assert session.last_activity_at is None
    with time_machine.travel(timezone.now() + timedelta(days=6)):
        assert session_token_expired(session) is False


@pytest.mark.django_db()
def test_session_expired_after_lifetime_despite_recent_activity():
    """The regression this exists to catch: chatting must not slide the window."""
    session = ExperimentSessionFactory.create()
    with time_machine.travel(timezone.now() + timedelta(days=7, hours=1)) as traveller:
        # The post_save signal on a human message updates last_activity_at on the DB row.
        ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="hi")
        session.refresh_from_db()
        assert session.last_activity_at is not None
        traveller.shift(timedelta(minutes=1))
        assert session_token_expired(session) is True


@pytest.mark.django_db()
def test_session_expired_once_older_than_lifetime():
    session = ExperimentSessionFactory.create()
    assert session_token_expired(session) is False
    with time_machine.travel(timezone.now() + timedelta(days=7, hours=1)):
        assert session_token_expired(session) is True


@pytest.mark.django_db()
def test_lifetime_setting_drives_expiry(settings):
    session = ExperimentSessionFactory.create()
    settings.CHAT_SESSION_TOKEN_LIFETIME = timedelta(hours=4)
    with time_machine.travel(timezone.now() + timedelta(hours=5)):
        assert session_token_expired(session) is True


def _session_with_channel_lifetime(lifetime):
    session = ExperimentSessionFactory.create()
    channel = session.experiment_channel
    channel.session_token_lifetime = lifetime
    channel.save()
    return session


@pytest.mark.django_db()
def test_channel_lifetime_overrides_the_global(settings):
    """A channel facing abuse can tighten below the global (D7)."""
    settings.CHAT_SESSION_TOKEN_LIFETIME = timedelta(days=7)
    session = _session_with_channel_lifetime(timedelta(hours=4))
    with time_machine.travel(timezone.now() + timedelta(hours=5)):
        assert session_token_expired(session) is True


@pytest.mark.django_db()
def test_channel_lifetime_may_also_loosen(settings):
    settings.CHAT_SESSION_TOKEN_LIFETIME = timedelta(hours=4)
    session = _session_with_channel_lifetime(timedelta(days=30))
    with time_machine.travel(timezone.now() + timedelta(days=8)):
        assert session_token_expired(session) is False


@pytest.mark.django_db()
def test_null_channel_lifetime_falls_back_to_the_global(settings):
    settings.CHAT_SESSION_TOKEN_LIFETIME = timedelta(hours=4)
    session = _session_with_channel_lifetime(None)
    with time_machine.travel(timezone.now() + timedelta(hours=5)):
        assert session_token_expired(session) is True


@pytest.mark.django_db()
def test_session_without_a_channel_falls_back_to_the_global(settings):
    settings.CHAT_SESSION_TOKEN_LIFETIME = timedelta(hours=4)
    session = ExperimentSessionFactory.create(experiment_channel=None, platform=ChannelPlatform.WEB)
    with time_machine.travel(timezone.now() + timedelta(hours=5)):
        assert session_token_expired(session) is True


def _legacy_token(session):
    """A token minted before the expiry claim existed."""
    return signing.dumps({"sid": str(session.external_id)}, salt=SESSION_TOKEN_SALT)


def _payload(token, session) -> dict:
    payload = parse_session_token(token, session.external_id)
    assert payload is not None
    return payload


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("channel_lifetime", "expected_lifetime"),
    [
        pytest.param(None, timedelta(hours=4), id="global-lifetime"),
        pytest.param(timedelta(minutes=30), timedelta(minutes=30), id="channel-override"),
    ],
)
def test_issued_token_carries_expiry(settings, channel_lifetime, expected_lifetime):
    settings.CHAT_SESSION_TOKEN_LIFETIME = timedelta(hours=4)
    session = _session_with_channel_lifetime(channel_lifetime)
    with time_machine.travel(timezone.now(), tick=False):
        payload = _payload(issue_session_token(session), session)
        assert payload["exp"] == int((timezone.now() + expected_lifetime).timestamp())


@pytest.mark.django_db()
def test_token_for_a_session_without_a_channel_carries_the_global_expiry(settings):
    settings.CHAT_SESSION_TOKEN_LIFETIME = timedelta(hours=4)
    session = ExperimentSessionFactory.create(experiment_channel=None, platform=ChannelPlatform.WEB)
    with time_machine.travel(timezone.now(), tick=False):
        payload = _payload(issue_session_token(session), session)
        assert payload["exp"] == int((timezone.now() + timedelta(hours=4)).timestamp())


@pytest.mark.django_db()
def test_parse_rejects_a_token_for_another_session():
    session = ExperimentSessionFactory.create()
    other = ExperimentSessionFactory.create(experiment=session.experiment)
    assert parse_session_token(issue_session_token(other), session.external_id) is None


@pytest.mark.django_db()
def test_token_expiry_is_measured_from_issuance_not_session_age():
    """Re-minting a token for an old session renews access: the claim, not the session's age, decides."""
    session = ExperimentSessionFactory.create()
    with time_machine.travel(timezone.now() + timedelta(days=30)):
        payload = _payload(issue_session_token(session), session)
        assert session_token_expired(session, payload) is False


@pytest.mark.django_db()
def test_token_expired_once_past_its_claim():
    session = ExperimentSessionFactory.create()
    payload = _payload(issue_session_token(session), session)
    assert session_token_expired(session, payload) is False
    with time_machine.travel(timezone.now() + timedelta(days=7, hours=1)):
        assert session_token_expired(session, payload) is True


@pytest.mark.django_db()
def test_token_without_expiry_claim_falls_back_to_session_age(settings):
    settings.CHAT_SESSION_TOKEN_LIFETIME = timedelta(hours=4)
    session = ExperimentSessionFactory.create()
    payload = _payload(_legacy_token(session), session)
    assert "exp" not in payload
    assert session_token_expired(session, payload) is False
    with time_machine.travel(timezone.now() + timedelta(hours=5)):
        assert session_token_expired(session, payload) is True
