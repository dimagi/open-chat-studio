"""What stands in the way of archiving each content resource, and what archiving changes (#4145)."""

import pytest

from apps.experiments.models import ConsentForm, SourceMaterial
from apps.utils.factories.experiment import ChatbotFactory, ConsentFormFactory, SourceMaterialFactory
from apps.utils.factories.pipelines import NodeFactory
from apps.utils.tests.clients import ApiTestClient


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
@pytest.mark.parametrize(
    ("auth_kwargs", "has_owner"),
    [
        pytest.param(
            {"auth_method": "oauth_client_credentials", "scopes": ["chatbots:write"]},
            False,
            id="machine-token-no-owner",
        ),
        pytest.param({}, True, id="human-credential-owns"),
    ],
)
def test_source_material_owner(team, auth_kwargs, has_owner):
    client = ApiTestClient(team.members.first(), team, **auth_kwargs)

    response = client.post("/api/v2/source-material/", {"topic": "Returns", "material": "30 days."}, format="json")

    assert response.status_code == 201, response.content
    expected_owner = team.members.first() if has_owner else None
    assert SourceMaterial.objects.get(pk=response.json()["id"]).owner == expected_owner
