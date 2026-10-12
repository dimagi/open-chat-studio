import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.db import IntegrityError
from django.http import Http404
from django.test import RequestFactory

from apps.chat.models import ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession
from apps.service_providers.models import LlmProvider
from apps.teams.backends import SUPER_ADMIN_GROUP
from apps.teams.models import Membership, Team
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.user import UserFactory
from apps.web import e2e_data, e2e_views
from apps.web.e2e_data import E2E_EMAIL_DOMAIN, create_e2e_data, delete_e2e_data


def _create(state: str) -> dict:
    return create_e2e_data(state=state, base_url="http://testserver")


@pytest.mark.django_db()
def test_create_team_makes_a_user_who_owns_a_new_team():
    data = _create("team")

    team = Team.objects.get(slug=data["team_slug"])
    user = get_user_model().objects.get(email=data["email"])
    assert user.check_password(data["password"])
    membership = Membership.objects.get(team=team)
    assert membership.user == user
    assert list(membership.groups.values_list("name", flat=True)) == [SUPER_ADMIN_GROUP]
    assert not LlmProvider.objects.filter(team=team).exists()
    assert data["team_url"] == f"/a/{team.slug}"


@pytest.mark.django_db()
def test_create_returns_a_logged_in_session_cookie():
    data = _create("team")

    user = get_user_model().objects.get(email=data["email"])
    session = SessionStore(session_key=data["session_cookie"]["value"])
    assert session["_auth_user_id"] == str(user.id)


@pytest.mark.django_db()
def test_each_create_makes_a_separate_user_and_team():
    first = _create("team")
    second = _create("team")

    assert first["email"] != second["email"]
    assert first["team_slug"] != second["team_slug"]


@pytest.mark.django_db()
def test_create_llm_provider_points_at_the_mock_llm():
    data = _create("llm_provider")

    provider = LlmProvider.objects.get(team__slug=data["team_slug"])
    assert provider.name == data["llm_provider_name"]
    assert provider.config["openai_api_base"] == "http://testserver/mock-llm/v1"
    assert not Experiment.objects.filter(team=provider.team).exists()


@pytest.mark.django_db()
def test_create_chatbot_uses_the_llm_provider():
    data = _create("chatbot")

    chatbot = Experiment.objects.get(team__slug=data["team_slug"])
    provider = LlmProvider.objects.get(team=chatbot.team)
    assert chatbot.name == data["chatbot_name"]
    assert data["chatbot_url"] == f"/a/{chatbot.team.slug}/chatbots/{chatbot.id}/"
    llm_node = chatbot.pipeline.node_set.get(type="LLMResponseWithPrompt")
    assert llm_node.params["llm_provider_id"] == provider.id
    assert not ExperimentSession.objects.filter(team=chatbot.team).exists()


@pytest.mark.django_db()
def test_create_session_adds_a_conversation_from_the_user():
    data = _create("session")

    session = ExperimentSession.objects.get(team__slug=data["team_slug"])
    assert session.participant.identifier == data["email"]
    assert session.participant.user.email == data["email"]
    messages = list(session.chat.messages.order_by("created_at").values_list("message_type", "content"))
    assert messages[0] == (ChatMessageType.HUMAN, data["user_message"])
    assert messages[1][0] == ChatMessageType.AI
    assert data["session_url"].endswith(f"/s/{session.external_id}/view/")


@pytest.mark.django_db()
def test_delete_removes_e2e_users_and_their_teams():
    data = _create("session")
    team_created_in_test = TeamFactory.create()
    MembershipFactory.create(team=team_created_in_test, user=get_user_model().objects.get(email=data["email"]))

    delete_e2e_data()

    assert not get_user_model().objects.filter(email=data["email"]).exists()
    assert not Team.objects.filter(slug=data["team_slug"]).exists()
    assert not Team.objects.filter(id=team_created_in_test.id).exists()


@pytest.mark.django_db()
def test_delete_keeps_other_users_and_teams_with_other_members():
    data = _create("team")
    other_user = UserFactory.create()
    shared_team = TeamFactory.create()
    MembershipFactory.create(team=shared_team, user=other_user)
    MembershipFactory.create(team=shared_team, user=get_user_model().objects.get(email=data["email"]))
    unrelated_team = TeamFactory.create()

    delete_e2e_data()

    assert get_user_model().objects.filter(id=other_user.id).exists()
    assert Team.objects.filter(id=shared_team.id).exists()
    assert Team.objects.filter(id=unrelated_team.id).exists()


@pytest.mark.django_db()
def test_create_view_points_the_provider_at_the_requesting_server(settings):
    settings.DEBUG = True
    request = RequestFactory().post("/e2e-data/create/", {"state": "llm_provider"}, HTTP_HOST="127.0.0.1:8010")

    response = e2e_views.create(request)

    data = json.loads(response.content)
    provider = LlmProvider.objects.get(team__slug=data["team_slug"])
    assert provider.config["openai_api_base"] == "http://127.0.0.1:8010/mock-llm/v1"


@pytest.mark.django_db()
def test_create_view_rejects_an_unknown_state(settings):
    settings.DEBUG = True
    request = RequestFactory().post("/e2e-data/create/", {"state": "nonsense"})

    assert e2e_views.create(request).status_code == 400


@pytest.mark.django_db()
def test_cleanup_view_deletes_e2e_data(settings):
    settings.DEBUG = True
    data = _create("team")

    response = e2e_views.cleanup(RequestFactory().post("/e2e-data/cleanup/"))

    assert response.status_code == 200
    assert not get_user_model().objects.filter(email=data["email"]).exists()


@pytest.mark.parametrize(
    "view", [pytest.param(e2e_views.create, id="create"), pytest.param(e2e_views.cleanup, id="cleanup")]
)
@pytest.mark.django_db()
def test_views_404_outside_debug_and_ci(settings, view):
    settings.DEBUG = False
    settings.IS_CI = False

    with pytest.raises(Http404):
        view(RequestFactory().post("/e2e-data/", {"state": "team"}))

    assert not get_user_model().objects.filter(email__endswith=f"@{E2E_EMAIL_DOMAIN}").exists()


@pytest.mark.parametrize(
    ("debug", "is_ci"),
    [
        pytest.param(True, False, id="debug"),
        pytest.param(False, True, id="ci"),
    ],
)
@pytest.mark.django_db()
def test_views_run_in_debug_or_ci(settings, debug, is_ci):
    settings.DEBUG = debug
    settings.IS_CI = is_ci

    response = e2e_views.create(RequestFactory().post("/e2e-data/create/", {"state": "team"}))

    assert response.status_code == 200


@pytest.mark.django_db()
def test_delete_retries_a_team_that_a_running_request_wrote_to():
    data = _create("team")
    real_delete = e2e_data.delete_object_with_auditing_of_related_objects
    attempts = []

    def fail_once(team):
        attempts.append(team)
        if len(attempts) == 1:
            raise IntegrityError("still referenced")
        real_delete(team)

    with patch.object(e2e_data, "delete_object_with_auditing_of_related_objects", side_effect=fail_once):
        delete_e2e_data()

    assert len(attempts) == 2
    assert not Team.objects.filter(slug=data["team_slug"]).exists()
