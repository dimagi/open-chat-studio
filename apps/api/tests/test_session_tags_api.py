import pytest
from rest_framework import status

from apps.annotations.models import Tag, TagCategories
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def experiment(db):
    return ExperimentFactory.create(team=TeamWithUsersFactory.create())


@pytest.fixture()
def session(experiment):
    return ExperimentSessionFactory.create(experiment=experiment)


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_add_tags_to_session(auth_method, session):
    user = session.team.members.first()
    client = ApiTestClient(user, session.team, auth_method=auth_method)

    url = f"/api/sessions/{session.external_id}/tags/"
    response = client.post(url, data={"tags": ["important", "reviewed"]}, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert set(response.json()["tags"]) == {"important", "reviewed"}

    session.refresh_from_db()
    assert set(session.chat.tags.values_list("name", flat=True)) == {"important", "reviewed"}

    tags = Tag.objects.filter(name__in=["important", "reviewed"])
    assert tags.count() == 2
    for tag in tags:
        assert tag.team == session.team
        assert tag.created_by == user


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_add_tags_to_session_creates_tags_if_not_exist(auth_method, session):
    user = session.team.members.first()
    client = ApiTestClient(user, session.team, auth_method=auth_method)

    assert not Tag.objects.filter(name="new_tag", team=session.team).exists()

    response = client.post(f"/api/sessions/{session.external_id}/tags/", data={"tags": ["new_tag"]}, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert Tag.objects.filter(name="new_tag", team=session.team).exists()


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_add_tags_to_session_idempotent(auth_method, session):
    user = session.team.members.first()
    client = ApiTestClient(user, session.team, auth_method=auth_method)
    url = f"/api/sessions/{session.external_id}/tags/"

    response = client.post(url, data={"tags": ["duplicate"]}, format="json")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["tags"] == ["duplicate"]

    response = client.post(url, data={"tags": ["duplicate"]}, format="json")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["tags"] == ["duplicate"]

    session.refresh_from_db()
    assert session.chat.tags.count() == 1


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_remove_tags_from_session(auth_method, session):
    user = session.team.members.first()
    team = session.team

    tags = Tag.objects.bulk_create(
        [
            Tag(name="tag1", slug="tag1", team=team, created_by=user),
            Tag(name="tag2", slug="tag2", team=team, created_by=user),
            Tag(name="tag3", slug="tag3", team=team, created_by=user),
        ]
    )
    for tag in tags:
        session.chat.add_tag(tag, team, user)
    assert session.chat.tags.count() == 3

    url = f"/api/sessions/{session.external_id}/tags/"
    client = ApiTestClient(user, team, auth_method=auth_method)
    response = client.delete(url, data={"tags": ["tag1", "tag3"]}, format="json")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["tags"] == ["tag2"]

    session.refresh_from_db()
    assert list(session.chat.tags.values_list("name", flat=True)) == ["tag2"]


@pytest.mark.django_db()
def test_remove_nonexistent_tags_from_session(session):
    user = session.team.members.first()
    client = ApiTestClient(user, session.team)
    url = f"/api/sessions/{session.external_id}/tags/"
    response = client.delete(url, data={"tags": ["nonexistent_tag"]}, format="json")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["tags"] == []


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        pytest.param({}, "Missing 'tags' in request", id="missing-tags-field"),
        pytest.param({"tags": "not_a_list"}, "'tags' must be a list", id="tags-not-a-list"),
        pytest.param({"tags": ["", "valid"]}, "'tags' must be a list of non-empty strings", id="empty-string"),
        pytest.param({"tags": ["  ", "valid"]}, "'tags' must be a list of non-empty strings", id="whitespace-only"),
        pytest.param({"tags": [123, "valid"]}, "'tags' must be a list of non-empty strings", id="non-string-values"),
    ],
)
def test_add_tags_input_validation(session, payload, expected_error):
    user = session.team.members.first()
    client = ApiTestClient(user, session.team)
    response = client.post(f"/api/sessions/{session.external_id}/tags/", data=payload, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert expected_error in response.json()["error"]


@pytest.mark.django_db()
def test_add_tags_session_not_found(experiment):
    user = experiment.team.members.first()
    client = ApiTestClient(user, experiment.team)
    response = client.post("/api/sessions/nonexistent-session-id/tags/", data={"tags": ["test"]}, format="json")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "Session not found" in response.json()["error"]


@pytest.mark.django_db()
def test_tags_endpoint_team_isolation(experiment):
    team2 = TeamWithUsersFactory.create()
    session2 = ExperimentSessionFactory.create(experiment=ExperimentFactory.create(team=team2))

    client1 = ApiTestClient(experiment.team.members.first(), experiment.team)
    response = client1.post(f"/api/sessions/{session2.external_id}/tags/", data={"tags": ["test"]}, format="json")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_delete_session_tags_does_not_remove_system_tags(auth_method, session):
    user = session.team.members.first()
    team = session.team

    user_tag = Tag.objects.create(
        name="important", slug="important-user", team=team, is_system_tag=False, category="", created_by=user
    )
    system_tag = Tag.objects.create(
        name="important", slug="important-system", team=team, is_system_tag=True, category=TagCategories.BOT_RESPONSE
    )

    session.chat.add_tag(user_tag, team, user)
    session.chat.add_tag(system_tag, team, user)
    assert session.chat.tags.count() == 2

    response = ApiTestClient(user, team, auth_method=auth_method).delete(
        f"/api/sessions/{session.external_id}/tags/", data={"tags": ["important"]}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK

    session.refresh_from_db()
    remaining = list(session.chat.tags.all())
    assert len(remaining) == 1
    assert remaining[0].id == system_tag.id
    assert remaining[0].is_system_tag is True
    assert response.json()["tags"] == ["important"]
