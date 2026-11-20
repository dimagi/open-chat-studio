"""Writes to fields the v2 sync API returns move `updated_at`, which is what the sync cursor pages by."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.annotations.models import CustomTaggedItem
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


def _apply_tag_change(tagged, tag, team, change):
    if change == "add":
        tagged.add_tag(tag, team=team)
    elif change == "remove":
        tagged.tags.remove(tag.name)
    else:
        tagged.tags.clear()


TAG_CHANGES = [pytest.param("add", id="add"), pytest.param("remove", id="remove"), pytest.param("clear", id="clear")]


@pytest.mark.django_db()
@pytest.mark.parametrize("change", TAG_CHANGES)
def test_message_tag_change_moves_the_message(message, change):
    tag = TagFactory.create(team=message.chat.team)
    if change != "add":
        message.add_tag(tag, team=message.chat.team)
    _age(ChatMessage, message)

    _apply_tag_change(message, tag, message.chat.team, change)

    assert _moved(ChatMessage, message)


@pytest.mark.django_db()
@pytest.mark.parametrize("change", TAG_CHANGES)
def test_chat_tag_change_moves_the_session(session, change):
    tag = TagFactory.create(team=session.team)
    if change != "add":
        session.chat.add_tag(tag, team=session.team)
    _age(ExperimentSession, session)

    _apply_tag_change(session.chat, tag, session.team, change)

    assert _moved(ExperimentSession, session)


@pytest.mark.django_db()
def test_removing_tag_rows_in_bulk_is_one_query(session, django_assert_num_queries):
    tag = TagFactory.create(team=session.team)
    for content in ["a", "b", "c"]:
        message = ChatMessage.objects.create(chat=session.chat, content=content, message_type=ChatMessageType.HUMAN)
        message.add_tag(tag, team=session.team)

    with django_assert_num_queries(1):
        CustomTaggedItem.objects.filter(tag=tag).delete()


@pytest.mark.django_db()
def test_update_fields_given_as_an_iterator_still_saves_the_change(message):
    _age(ChatMessage, message)
    message.metadata = {"ocs_attachment_file_ids": [2]}

    message.save(update_fields=iter(["metadata"]))

    stored = ChatMessage.objects.get(pk=message.pk)
    assert stored.metadata == {"ocs_attachment_file_ids": [2]}
    assert stored.updated_at > PAST


@pytest.mark.django_db()
def test_saving_a_deferred_instance_moves_updated_at(message):
    _age(ChatMessage, message)
    loaded = ChatMessage.objects.only("id", "metadata").get(pk=message.pk)
    loaded.metadata = {"ocs_attachment_file_ids": [3]}

    loaded.save()

    stored = ChatMessage.objects.get(pk=message.pk)
    assert stored.metadata == {"ocs_attachment_file_ids": [3]}
    assert stored.updated_at > PAST


@pytest.mark.django_db()
def test_renaming_a_tag_moves_the_rows_it_tags(session, message):
    tag = TagFactory.create(team=session.team, name="billing")
    message.add_tag(tag, team=session.team)
    session.chat.add_tag(tag, team=session.team)
    _age(ChatMessage, message)
    _age(ExperimentSession, session)

    tag.name = "invoicing"
    tag.save()

    assert _moved(ChatMessage, message)
    assert _moved(ExperimentSession, session)
