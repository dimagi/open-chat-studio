import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ExperimentSessionFactory

migration = importlib.import_module("apps.experiments.migrations.0141_experimentsession_session_token_required")


@pytest.mark.django_db()
def test_new_sessions_default_to_token_required():
    session = ExperimentSessionFactory.create()
    assert session.session_token_required is True


@pytest.mark.django_db()
def test_backfill_by_activity():
    stale = ExperimentSessionFactory.create()
    ChatMessage.objects.create(chat=stale.chat, message_type=ChatMessageType.HUMAN, content="old")
    stale_no_messages = ExperimentSessionFactory.create()
    # push both into the past (created_at is auto_now_add, so update via queryset)
    two_days_ago = timezone.now() - timedelta(days=2)
    ExperimentSession.objects.filter(id__in=[stale.id, stale_no_messages.id]).update(created_at=two_days_ago)
    ChatMessage.objects.filter(chat=stale.chat).update(created_at=two_days_ago)

    active = ExperimentSessionFactory.create()
    old_session_recent_message = ExperimentSessionFactory.create()
    ExperimentSession.objects.filter(id=old_session_recent_message.id).update(created_at=two_days_ago)
    ChatMessage.objects.create(chat=old_session_recent_message.chat, message_type=ChatMessageType.HUMAN, content="new")

    migration.backfill_session_token_required(django_apps, None)

    def flag(session):
        return ExperimentSession.objects.get(id=session.id).session_token_required

    assert flag(stale) is True
    assert flag(stale_no_messages) is True
    assert flag(active) is False  # created within 24h
    assert flag(old_session_recent_message) is False  # message within 24h
