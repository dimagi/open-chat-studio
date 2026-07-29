import json

import pytest
from django.urls import reverse

from apps.annotations.models import UserComment
from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team():
    return TeamWithUsersFactory.create()


@pytest.fixture()
def chat(team, db):
    session = ExperimentSessionFactory.create(team=team, chat__team=team)
    return session.chat


def _link_comment_to_item(client, message: ChatMessage, comment: str):
    team = message.chat.team
    user = team.members.first()
    client.login(username=user.username, password="password")

    data = {"comment": comment, "object_info": message.object_info}
    link_url = reverse("annotations:add_comment", kwargs={"team_slug": team.slug})
    client.post(link_url, data=data)


@pytest.mark.django_db()
def test_link_comment_view(chat, client):
    message = ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Sqeezy")
    _link_comment_to_item(client, message=message, comment="this is a test")
    _link_comment_to_item(client, message=message, comment="this is a second test")
    assert len(message.get_user_comments()) == 2
    assert message.comments.first().comment == "this is a test"
    assert message.comments.last().comment == "this is a second test"


@pytest.mark.django_db()
def test_unlink_comment_view(chat, client):
    message = ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Sqeezy")
    _link_comment_to_item(client, message=message, comment="this is a test")
    user_comment = message.comments.first()
    assert user_comment.comment == "this is a test"

    data = {"comment_id": user_comment.id, "object_info": message.object_info}
    url = reverse("annotations:remove_comment", kwargs={"team_slug": chat.team.slug})
    client.post(url, data=data)

    assert message.comments.count() == 0


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "accessor",
    [
        pytest.param(lambda: UserComment, id="via-class"),
        pytest.param(lambda: UserComment(), id="via-instance"),
    ],
)
def test_add_for_model_is_a_real_staticmethod(chat, accessor):
    """`add_for_model` must not bind an instance to its `model` argument.

    Stacking `@transaction.atomic()` above `@staticmethod` wraps the descriptor rather than
    the function, leaving a plain function on the class — instance access then passes the
    instance as `model` and the call fails with a TypeError.
    """
    message = ChatMessage.objects.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Sqeezy")
    user = chat.team.members.first()

    comment = accessor().add_for_model(message, comment="a comment", added_by=user, team=chat.team)

    assert comment is not None
    assert comment.content_object == message


@pytest.mark.django_db()
def test_422_when_entity_doesn_not_support_comments(chat, client):
    team = chat.team
    user = team.members.first()
    client.login(username=user.username, password="password")

    # The team model does not support comments, so let's use this model
    data = {"comment": "testing", "object_info": json.dumps({"id": team.id, "app": "teams", "model_name": "team"})}
    link_url = reverse("annotations:add_comment", kwargs={"team_slug": team.slug})
    response = client.post(link_url, data=data)
    assert response.status_code == 422
