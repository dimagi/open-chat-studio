"""POST /api/v2/chatbots/ -- create a working draft (#4139, spec 5.1)."""

import unicodedata

import pytest
from django.urls import reverse

from apps.chatbots.version_resolver import resolve_published_or_working
from apps.experiments.models import Experiment
from apps.pipelines.build_state import pipeline_build_state
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient

CREATE_URL = "/api/v2/chatbots/"


@pytest.fixture()
def team(db):
    """A provider alone is enough to seed an LLM node.

    `get_first_llm_provider_model` filters by the provider type's *default* model name
    (`gpt-4.1-mini` for openai), and migration `service_providers.0020` seeds those default models
    as global rows, so it resolves without a team-owned LlmProviderModel. Creating one with
    `LlmProviderModelFactory` would in fact be filtered straight back out, since that factory names
    models `test-model-N`.
    """
    team = TeamWithUsersFactory.create()
    LlmProviderFactory.create(team=team)
    return team


@pytest.fixture()
def client(team):
    return ApiTestClient(team.members.first(), team)


def test_create_url_is_the_registered_list_route():
    assert reverse("api:v2:chatbot-list") == CREATE_URL


@pytest.mark.django_db()
def test_options_on_the_collection_describes_it(client):
    """DRF's OPTIONS metadata re-checks permissions against a `clone_request`, which does not carry
    the team the authenticator pinned on the DRF request. Adding POST to this viewset put that path
    in reach for the first time, and `DjangoModelPermissions` calls `get_queryset()` on it."""
    response = client.options(CREATE_URL)

    assert response.status_code == 200
    assert response.json()["name"] == "Chatbot List"


@pytest.mark.django_db()
def test_options_advertises_the_body_that_post_actually_accepts(client):
    """OPTIONS is how an agent discovers the request body, and `self.action` is "metadata" for the
    whole of an OPTIONS request -- so without resolving on the described method instead, this
    advertises the *read* serializer: `id`, `url`, `version_number` and `versions`, four keys POST
    then rejects as unrecognised, and no `description`, the one optional key it does take."""
    body = client.options(CREATE_URL).json()["actions"]["POST"]

    assert set(body) == {"name", "description"}
    assert body["name"]["required"] is True
    assert body["description"]["required"] is False


@pytest.mark.django_db()
def test_create_returns_exactly_the_three_spec_keys(client):
    response = client.post(CREATE_URL, {"name": "Connect Interviews Bot"}, format="json")

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "pipeline_id", "version_number"}
    assert body["version_number"] == 1

    chatbot = Experiment.objects.get(public_id=body["id"])
    assert chatbot.name == "Connect Interviews Bot"
    assert chatbot.pipeline_id == body["pipeline_id"]


@pytest.mark.django_db()
def test_create_seeds_a_start_llm_end_pipeline(client, team):
    response = client.post(CREATE_URL, {"name": "Seeded"}, format="json")

    chatbot = Experiment.objects.get(public_id=response.json()["id"])
    node_types = sorted(node.type for node in chatbot.pipeline.node_set.all())
    assert node_types == ["EndNode", "LLMResponseWithPrompt", "StartNode"]
    assert pipeline_build_state(chatbot.pipeline)["pipeline_valid"] is True


@pytest.mark.django_db()
def test_create_publishes_nothing(client):
    """Unlike the UI's CreateChatbot, the API leaves the bot unpublished -- it is still reachable
    because resolve_published_or_working falls back to the working version."""
    response = client.post(CREATE_URL, {"name": "Unpublished"}, format="json")

    chatbot = Experiment.objects.get(public_id=response.json()["id"])
    assert chatbot.versions.count() == 0
    assert resolve_published_or_working(chatbot) == chatbot


@pytest.mark.django_db()
def test_create_without_an_llm_provider_yields_an_invalid_shell():
    """With no provider the seed has no middle node and therefore no edges, so End is unreachable.
    That is reported, not rejected (spec W6, "lenient on structure")."""
    team = TeamWithUsersFactory.create()
    client = ApiTestClient(team.members.first(), team)

    response = client.post(CREATE_URL, {"name": "No provider"}, format="json")

    assert response.status_code == 201
    chatbot = Experiment.objects.get(public_id=response.json()["id"])
    assert sorted(node.type for node in chatbot.pipeline.node_set.all()) == ["EndNode", "StartNode"]
    assert pipeline_build_state(chatbot.pipeline)["pipeline_valid"] is False


@pytest.mark.django_db()
def test_create_normalizes_the_name_to_nfc(client):
    """Mirrors CreateChatbot.form_valid: "e" + combining acute becomes the single code point.

    Both forms are built with `unicodedata` rather than written as literals. As literals the
    difference is invisible in the source, so one stray editor normalization would quietly turn
    this into a tautology that passes while testing nothing.
    """
    decomposed = unicodedata.normalize("NFD", "Cafe\u0301 bot")
    composed = unicodedata.normalize("NFC", "Cafe\u0301 bot")
    assert decomposed != composed  # guards the guard

    response = client.post(CREATE_URL, {"name": decomposed}, format="json")

    chatbot = Experiment.objects.get(public_id=response.json()["id"])
    assert chatbot.name == composed


@pytest.mark.django_db()
def test_a_name_that_nfc_lengthens_past_the_column_is_a_400(client):
    """NFC can make a string *longer*: U+0958 is a composition exclusion, so it normalises to the
    two code points U+0915 U+093C. Normalising after `max_length` ran would clear the check and then
    overflow `varchar(128)` on insert -- a 500 for what is only an over-long name."""
    over_long = "क़" * 128
    assert len(unicodedata.normalize("NFC", over_long)) > 128  # guards the guard

    assert client.post(CREATE_URL, {"name": over_long}, format="json").status_code == 400


@pytest.mark.django_db()
def test_create_requires_a_name(client):
    assert client.post(CREATE_URL, {}, format="json").status_code == 400


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "sent",
    [
        pytest.param({}, id="omitted"),
        pytest.param({"description": ""}, id="blank"),
        pytest.param({"description": None}, id="null"),
    ],
)
def test_create_stores_an_absent_description_as_blank(client, sent):
    """`Experiment.description` is nullable for historical reasons, but the UI form only ever writes
    "". Accepting null and normalising it keeps one representation in the column -- and keeps the
    PATCH twin, which has to accept a null echoed back from a legacy row, symmetrical with this."""
    response = client.post(CREATE_URL, {"name": "Bot", **sent}, format="json")

    assert response.status_code == 201, response.content
    assert Experiment.objects.get(public_id=response.json()["id"]).description == ""


@pytest.mark.django_db()
def test_an_unrecognised_key_is_a_400_naming_it(client):
    """Silently dropping it would create the chatbot minus the description, and report success."""
    response = client.post(CREATE_URL, {"name": "Typo", "descriptoin": "oops"}, format="json")

    assert response.status_code == 400
    assert "descriptoin" in response.json()
    assert not Experiment.objects.filter(name="Typo").exists()


@pytest.mark.django_db()
def test_read_only_key_cannot_create(team):
    client = ApiTestClient(team.members.first(), team, read_only=True)
    assert client.post(CREATE_URL, {"name": "Nope"}, format="json").status_code == 403


@pytest.mark.django_db()
def test_oauth_token_without_the_write_scope_cannot_create(team):
    client = ApiTestClient(team.members.first(), team, auth_method="oauth", scopes=["chatbots:read"])
    assert client.post(CREATE_URL, {"name": "Nope"}, format="json").status_code == 403


@pytest.mark.django_db()
def test_machine_token_creates_an_ownerless_chatbot(team):
    """A client-credentials token has no user, so the row it creates has no owner (Task 2)."""
    client = ApiTestClient(
        team.members.first(), team, auth_method="oauth_client_credentials", scopes=["chatbots:write"]
    )

    response = client.post(CREATE_URL, {"name": "Headless"}, format="json")

    assert response.status_code == 201
    assert Experiment.objects.get(public_id=response.json()["id"]).owner is None
