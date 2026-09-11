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
)
from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_token_round_trip():
    session = ExperimentSessionFactory.create()
    payload = parse_session_token(issue_session_token(session), session.external_id)
    assert payload is not None
    assert payload["sid"] == str(session.external_id)


@pytest.mark.django_db()
def test_tampered_token_rejected():
    session = ExperimentSessionFactory.create()
    token = issue_session_token(session)
    assert parse_session_token(token[:-2] + "xx", session.external_id) is None


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("not-a-token", id="garbage"),
        pytest.param(None, id="none"),
        pytest.param("", id="empty"),
    ],
)
def test_malformed_token_rejected(token):
    assert parse_session_token(token, "some-id") is None


@pytest.mark.django_db()
def test_token_for_other_session_rejected():
    session = ExperimentSessionFactory.create()
    other = ExperimentSessionFactory.create(experiment=session.experiment)
    token = issue_session_token(other)
    assert parse_session_token(token, session.external_id) is None


@pytest.mark.django_db()
def test_wrong_salt_rejected():
    """A value signed elsewhere in the app with a different salt must not validate."""
    session = ExperimentSessionFactory.create()
    forged = signing.dumps({"sid": str(session.external_id)}, salt="other-salt")
    assert parse_session_token(forged, session.external_id) is None
    # sanity: the real salt is what issue_session_token uses
    assert SESSION_TOKEN_SALT == "ocs.chat.session-token"


def _payload(token, session) -> dict:
    payload = parse_session_token(token, session.external_id)
    assert payload is not None
    return payload


def _issued_payload(session) -> dict:
    return _payload(issue_session_token(session), session)


@pytest.mark.django_db()
def test_dormant_session_within_lifetime_not_expired():
    """Dormancy alone expires nothing; only the token's age does."""
    session = ExperimentSessionFactory.create()
    payload = _issued_payload(session)
    assert session.last_activity_at is None
    with time_machine.travel(timezone.now() + timedelta(days=6)):
        assert session_token_expired(session, payload) is False


@pytest.mark.django_db()
def test_token_expired_after_lifetime_despite_recent_activity():
    """The regression this exists to catch: chatting must not slide the window."""
    session = ExperimentSessionFactory.create()
    payload = _issued_payload(session)
    with time_machine.travel(timezone.now() + timedelta(days=7, hours=1)) as traveller:
        # The post_save signal on a human message updates last_activity_at on the DB row.
        ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="hi")
        session.refresh_from_db()
        assert session.last_activity_at is not None
        traveller.shift(timedelta(minutes=1))
        assert session_token_expired(session, payload) is True


@pytest.mark.django_db()
def test_lifetime_setting_drives_expiry(settings):
    settings.CHAT_SESSION_TOKEN_LIFETIME = timedelta(hours=4)
    session = ExperimentSessionFactory.create()
    payload = _issued_payload(session)
    with time_machine.travel(timezone.now() + timedelta(hours=5)):
        assert session_token_expired(session, payload) is True


def _session_with_channel_lifetime(lifetime):
    session = ExperimentSessionFactory.create()
    channel = session.experiment_channel
    channel.session_token_lifetime = lifetime
    channel.save()
    return session


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("global_lifetime", "channel_lifetime", "travel", "expired"),
    [
        pytest.param(timedelta(days=7), timedelta(hours=4), timedelta(hours=5), True, id="channel-tightens"),
        pytest.param(timedelta(hours=4), timedelta(days=30), timedelta(days=8), False, id="channel-loosens"),
        pytest.param(timedelta(hours=4), None, timedelta(hours=5), True, id="null-channel-uses-global"),
    ],
)
def test_channel_lifetime_overrides_the_global(settings, global_lifetime, channel_lifetime, travel, expired):
    settings.CHAT_SESSION_TOKEN_LIFETIME = global_lifetime
    session = _session_with_channel_lifetime(channel_lifetime)
    payload = _issued_payload(session)
    with time_machine.travel(timezone.now() + travel):
        assert session_token_expired(session, payload) is expired


@pytest.mark.django_db()
def test_session_without_a_channel_falls_back_to_the_global(settings):
    settings.CHAT_SESSION_TOKEN_LIFETIME = timedelta(hours=4)
    session = ExperimentSessionFactory.create(experiment_channel=None, platform=ChannelPlatform.WEB)
    payload = _issued_payload(session)
    with time_machine.travel(timezone.now() + timedelta(hours=5)):
        assert session_token_expired(session, payload) is True


@pytest.mark.django_db()
def test_tightening_the_lifetime_does_not_shorten_an_issued_token(settings):
    """The lifetime is fixed at issuance; only tokens minted after the change are shorter."""
    settings.CHAT_SESSION_TOKEN_LIFETIME = timedelta(days=7)
    session = ExperimentSessionFactory.create()
    payload = _issued_payload(session)
    settings.CHAT_SESSION_TOKEN_LIFETIME = timedelta(hours=1)
    with time_machine.travel(timezone.now() + timedelta(hours=2)):
        assert session_token_expired(session, payload) is False
        assert session_token_expired(session, _issued_payload(session)) is False


def _legacy_token(session):
    """A token with no `exp` claim."""
    return signing.dumps({"sid": str(session.external_id)}, salt=SESSION_TOKEN_SALT)


def _session_without_channel(_lifetime):
    return ExperimentSessionFactory.create(experiment_channel=None, platform=ChannelPlatform.WEB)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("make_session", "channel_lifetime", "expected_lifetime"),
    [
        pytest.param(_session_with_channel_lifetime, None, timedelta(hours=4), id="global-lifetime"),
        pytest.param(
            _session_with_channel_lifetime, timedelta(minutes=30), timedelta(minutes=30), id="channel-override"
        ),
        pytest.param(_session_without_channel, None, timedelta(hours=4), id="no-channel"),
    ],
)
def test_issued_token_carries_expiry(settings, make_session, channel_lifetime, expected_lifetime):
    settings.CHAT_SESSION_TOKEN_LIFETIME = timedelta(hours=4)
    session = make_session(channel_lifetime)
    with time_machine.travel(timezone.now(), tick=False):
        payload = _payload(issue_session_token(session), session)
        assert payload["exp"] == int((timezone.now() + expected_lifetime).timestamp())


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
