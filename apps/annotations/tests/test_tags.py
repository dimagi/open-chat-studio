import json
import unicodedata

import pytest
from django.urls import reverse

from apps.annotations.models import CustomTaggedItem, Tag
from apps.chat.models import Chat
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team():
    return TeamWithUsersFactory()


@pytest.fixture()
def chat(team, db):
    session = ExperimentSessionFactory(team=team, chat__team=team)
    return session.chat


@pytest.fixture()
def tag(team, db):
    user = team.members.first()
    return Tag.objects.create(name="testing", created_by=user, team=team)


@pytest.mark.django_db()
def test_edit_tag(tag, client):
    team = tag.team
    user = team.members.first()
    client.login(username=user.username, password="password")

    edit_url = reverse("annotations:tag_edit", kwargs={"team_slug": team.slug, "pk": tag.id})
    client.post(edit_url, data={"name": "new_name"})
    tag.refresh_from_db()
    assert tag.name == "new_name"


def _link_tag_to_item(client, tag: Tag, chat: Chat):
    team = tag.team
    user = team.members.first()
    client.login(username=user.username, password="password")
    data = {"tag_name": tag.name, "object_info": chat.object_info}
    link_url = reverse("annotations:link_tag", kwargs={"team_slug": team.slug})
    client.post(link_url, data=data)
    assert CustomTaggedItem.objects.filter(team=team, tag=tag, user=user).exists() is True


@pytest.mark.django_db()
def test_delete_tag_deletes_through_model(tag, chat, client):
    _link_tag_to_item(client, tag=tag, chat=chat)

    client.delete(reverse("annotations:tag_delete", kwargs={"team_slug": tag.team.slug, "pk": tag.id}), data={})
    assert Tag.objects.filter(name=tag.name, team=tag.team).exists() is False
    assert CustomTaggedItem.objects.filter(team=tag.team, tag=tag).exists() is False


@pytest.mark.django_db()
def test_link_tag(tag, chat, client):
    _link_tag_to_item(client, tag=tag, chat=chat)


@pytest.mark.django_db()
def test_tags_with_same_name_can_be_used_in_different_teams(client):
    """This simply ensures that different teams can use tags with the same name"""
    team1 = TeamWithUsersFactory()
    team2 = TeamWithUsersFactory()
    user1 = team1.members.first()
    user2 = team2.members.first()
    session1 = ExperimentSessionFactory(team=team1, chat__team=team1)
    session2 = ExperimentSessionFactory(team=team2, chat__team=team2)
    chat1 = session1.chat
    chat2 = session2.chat
    tag1 = Tag.objects.create(name="testing", created_by=user1, team=team1)
    tag2 = Tag.objects.create(name="testing", created_by=user2, team=team2)

    _link_tag_to_item(client, tag=tag1, chat=chat1)  # ty: ignore[invalid-argument-type]
    _link_tag_to_item(client, tag=tag2, chat=chat2)  # ty: ignore[invalid-argument-type]
    chat1.tags.get(name=tag1)
    chat2.tags.get(name=tag1)


@pytest.mark.django_db()
def test_new_tag_created_when_linking(chat, client):
    team = chat.team
    tag_name = "super cool"
    assert Tag.objects.filter(name=tag_name, team=team).exists() is False

    user = team.members.first()
    client.login(username=user.username, password="password")
    data = {"tag_name": tag_name, "object_info": chat.object_info}

    link_url = reverse("annotations:link_tag", kwargs={"team_slug": team.slug})
    client.post(link_url, data=data)
    tag = Tag.objects.get(name=tag_name, team=team)
    assert tag is not None
    assert CustomTaggedItem.objects.filter(team=team, tag=tag, user=user).exists() is True


@pytest.mark.django_db()
def test_unlink_tag(tag, chat, client):
    _link_tag_to_item(client, tag=tag, chat=chat)
    team = tag.team
    user = team.members.first()

    data = {"tag_name": tag.name, "object_info": chat.object_info}
    unlink_url = reverse("annotations:unlink_tag", kwargs={"team_slug": team.slug})
    client.post(unlink_url, data=data)

    assert CustomTaggedItem.objects.filter(team=team, tag=tag, user=user).exists() is False


@pytest.mark.django_db()
def test_link_tag_returns_404(tag, client):
    team = tag.team
    user = team.members.first()
    client.login(username=user.username, password="password")
    object_info_json = json.dumps({"app": "non-existent", "model_name": "chat", "id": 1})
    data = {"tag_name": tag.name, "object_info": object_info_json}
    response = client.post(reverse("annotations:link_tag", kwargs={"team_slug": tag.team.slug}), data=data)
    assert response.status_code == 404


@pytest.mark.django_db()
def test_unlink_tag_returns_404(tag, client):
    team = tag.team
    user = team.members.first()
    client.login(username=user.username, password="password")
    object_info_json = json.dumps({"app": "non-existent", "model_name": "chat", "id": 1})
    data = {"tag_name": tag.name, "object_info": object_info_json}
    response = client.post(reverse("annotations:unlink_tag", kwargs={"team_slug": tag.team.slug}), data=data)
    assert response.status_code == 404


@pytest.mark.django_db()
def test_create_duplicate_tag_error(client):
    team = TeamWithUsersFactory()
    user = team.members.first()
    client.login(username=user.username, password="password")

    tag_name = "duplicate_tag"
    Tag.objects.create(name=tag_name, created_by=user, team=team)

    url = reverse("annotations:tag_new", kwargs={"team_slug": team.slug})
    response = client.post(url, data={"name": tag_name})

    assert response.status_code == 200
    assert "A tag with this name already exists" in response.content.decode()
    assert Tag.objects.filter(name=tag_name, team=team).count() == 1


@pytest.mark.django_db()
def test_unicode_normalization_prevents_duplicates(client):
    team = TeamWithUsersFactory()
    user = team.members.first()

    client.login(username=user.username, password="password")
    Tag.objects.create(name="café", created_by=user, team=team)

    # café can be represented in Unicode as "cafe\u0301" or as "café"
    url = reverse("annotations:tag_new", kwargs={"team_slug": team.slug})
    response = client.post(url, data={"name": "cafe\u0301"})

    assert response.status_code == 200
    assert "A tag with this name already exists" in response.content.decode()

    assert Tag.objects.filter(name=unicodedata.normalize("NFC", "café"), team=team).count() == 1
