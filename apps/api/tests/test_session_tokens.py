from datetime import timedelta

import pytest
import time_machine
from django.core import signing
from django.utils import timezone

from apps.api.session_tokens import (
    SESSION_TOKEN_SALT,
    issue_session_token,
    session_token_expired,
    validate_session_token,
)
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
def test_session_not_expired_with_recent_activity():
    session = ExperimentSessionFactory.create()
    # The post_save signal on a human message updates last_activity_at on the DB row.
    ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="hi")
    session.refresh_from_db()
    assert session.last_activity_at is not None
    assert session_token_expired(session) is False


@pytest.mark.django_db()
def test_session_expired_after_inactivity_window():
    session = ExperimentSessionFactory.create()
    ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="hi")
    session.refresh_from_db()
    with time_machine.travel(timezone.now() + timedelta(days=7, hours=1)):
        assert session_token_expired(session) is True


@pytest.mark.django_db()
def test_session_with_no_activity_uses_created_at():
    session = ExperimentSessionFactory.create()
    assert session.last_activity_at is None
    assert session_token_expired(session) is False
    with time_machine.travel(timezone.now() + timedelta(days=7, hours=1)):
        assert session_token_expired(session) is True
