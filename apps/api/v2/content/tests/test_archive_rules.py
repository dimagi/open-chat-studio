"""What stands in the way of archiving each content resource, and what archiving changes (#4145)."""

import pytest

from apps.experiments.models import ConsentForm, SourceMaterial
from apps.utils.factories.experiment import ChatbotFactory, ConsentFormFactory, SourceMaterialFactory
from apps.utils.factories.pipelines import NodeFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def client(team):
    return ApiTestClient(team.members.first(), team)


@pytest.mark.django_db()
def test_source_material_in_use_is_not_archived(client, team):
    material = SourceMaterialFactory.create(team=team)
    chatbot = ChatbotFactory.create(team=team)
    NodeFactory.create(pipeline=chatbot.pipeline, params={"source_material_id": material.id})

    response = client.delete(f"/api/v2/source-material/{material.id}/")

    assert response.status_code == 409, response.content
    material.refresh_from_db()
    assert material.is_archived is False


@pytest.mark.django_db()
def test_the_default_consent_form_is_not_archived(client, team):
    default = ConsentForm.get_default(team)

    response = client.delete(f"/api/v2/consent-forms/{default.id}/")

    assert response.status_code == 409, response.content
    default.refresh_from_db()
    assert default.is_archived is False


@pytest.mark.django_db()
def test_archiving_a_consent_form_moves_its_chatbots_onto_the_default(client, team):
    default = ConsentForm.get_default(team)
    form = ConsentFormFactory.create(team=team)
    chatbot = ChatbotFactory.create(team=team, consent_form=form)

    response = client.delete(f"/api/v2/consent-forms/{form.id}/")

    assert response.status_code == 200, response.content
    chatbot.refresh_from_db()
    assert chatbot.consent_form == default


@pytest.mark.django_db()
def test_created_source_material_can_be_wired_into_a_node(client, team):
    """The id the create call returns is one the pipeline façade accepts as a node reference."""
    chatbot = ChatbotFactory.create(team=team)
    material_id = client.post(
        "/api/v2/source-material/", {"topic": "Returns", "material": "30 days."}, format="json"
    ).json()["id"]

    response = client.post(
        f"/api/v2/chatbots/{chatbot.public_id}/pipeline/nodes/",
        {"type": "LLMResponseWithPrompt", "params": {"source_material_id": material_id}},
        format="json",
    )

    assert response.status_code == 201, response.content
    assert response.json()["node"]["params"]["source_material_id"] == material_id


@pytest.mark.django_db()
def test_a_machine_token_creates_source_material_with_no_owner(team):
    client = ApiTestClient(
        team.members.first(), team, auth_method="oauth_client_credentials", scopes=["chatbots:write"]
    )

    response = client.post("/api/v2/source-material/", {"topic": "Returns", "material": "30 days."}, format="json")

    assert response.status_code == 201, response.content
    assert SourceMaterial.objects.get(pk=response.json()["id"]).owner is None


@pytest.mark.django_db()
def test_a_human_credential_owns_the_source_material_it_creates(client, team):
    response = client.post("/api/v2/source-material/", {"topic": "Returns", "material": "30 days."}, format="json")

    assert SourceMaterial.objects.get(pk=response.json()["id"]).owner == team.members.first()
