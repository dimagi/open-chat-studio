"""v2's 404s name a resource as the API does, not by its model's class name."""

import pytest

from apps.utils.factories.experiment import ChatbotFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

MISSING_UUID = "123e4567-e89b-12d3-a456-426614174000"


@pytest.fixture()
def chatbot(db):
    return ChatbotFactory.create(team=TeamWithUsersFactory.create())


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("path", "detail"),
    [
        pytest.param(f"/api/v2/chatbots/{MISSING_UUID}/", "No chatbot matches the given query.", id="chatbot"),
        pytest.param("/api/v2/source-material/0/", "No source material matches the given query.", id="source-material"),
        pytest.param("/api/v2/consent-forms/0/", "No consent form matches the given query.", id="consent-form"),
        pytest.param(
            "/api/v2/chatbots/{chatbot}/timeout-triggers/0/",
            "No timeout trigger matches the given query.",
            id="timeout",
        ),
        pytest.param(
            "/api/v2/chatbots/{chatbot}/static-triggers/0/", "No static trigger matches the given query.", id="static"
        ),
        pytest.param(
            "/api/v2/chatbots/{chatbot}/scheduled-triggers/0/",
            "No scheduled trigger matches the given query.",
            id="scheduled",
        ),
    ],
)
def test_a_404_names_the_resource_as_the_api_does(chatbot, path, detail):
    client = ApiTestClient(chatbot.team.members.first(), chatbot.team)

    # The trigger routes offer no GET; a DELETE of a missing trigger answers the same 404.
    method = client.delete if "-triggers/" in path else client.get
    response = method(path.format(chatbot=chatbot.public_id))

    assert response.status_code == 404
    assert response.json() == {"detail": detail}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/api/v2/chatbots/not-a-uuid/", id="chatbot"),
        pytest.param("/api/v2/source-material/abc/", id="source-material"),
        pytest.param("/api/v2/consent-forms/abc/", id="consent-form"),
    ],
)
def test_a_malformed_id_answers_drfs_default_404(chatbot, path):
    client = ApiTestClient(chatbot.team.members.first(), chatbot.team)

    response = client.get(path)

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found."}
