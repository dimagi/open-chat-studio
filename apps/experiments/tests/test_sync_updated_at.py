"""Writes to fields the v2 sync API returns move `updated_at`, which is what the sync cursor pages by."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession
from apps.utils.factories.annotations import TagFactory
from apps.utils.factories.experiment import ExperimentSessionFactory

PAST = timezone.now() - timedelta(days=1)


def _age(model, obj):
    model.objects.filter(pk=obj.pk).update(updated_at=PAST)
    obj.refresh_from_db()


def _moved(model, obj) -> bool:
    return model.objects.get(pk=obj.pk).updated_at > PAST


@pytest.fixture()
def session():
    return ExperimentSessionFactory.create()


@pytest.fixture()
def message(session):
    return ChatMessage.objects.create(chat=session.chat, content="hi", message_type=ChatMessageType.HUMAN)


@pytest.mark.django_db()
def test_partial_session_save_moves_updated_at(session):
    _age(ExperimentSession, session)
    session.state = {"step": 2}

    session.save(update_fields=["state"])

    assert _moved(ExperimentSession, session)


@pytest.mark.django_db()
def test_partial_message_save_moves_updated_at(message):
    _age(ChatMessage, message)
    message.metadata = {"ocs_attachment_file_ids": [1]}

    message.save(update_fields=["metadata"])

    assert _moved(ChatMessage, message)


@pytest.mark.django_db()
@pytest.mark.parametrize("remove", [pytest.param(False, id="add"), pytest.param(True, id="remove")])
def test_message_tag_change_moves_the_message(message, remove):
    tag = TagFactory.create(team=message.chat.team)
    if remove:
        message.add_tag(tag, team=message.chat.team)
    _age(ChatMessage, message)

    if remove:
        message.tags.remove(tag)
    else:
        message.add_tag(tag, team=message.chat.team)

    assert _moved(ChatMessage, message)


@pytest.mark.django_db()
@pytest.mark.parametrize("remove", [pytest.param(False, id="add"), pytest.param(True, id="remove")])
def test_chat_tag_change_moves_the_session(session, remove):
    tag = TagFactory.create(team=session.team)
    if remove:
        session.chat.add_tag(tag, team=session.team)
    _age(ExperimentSession, session)

    if remove:
        session.chat.tags.remove(tag)
    else:
        session.chat.add_tag(tag, team=session.team)

    assert _moved(ExperimentSession, session)
