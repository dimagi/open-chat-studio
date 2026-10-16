"""What stands in the way of archiving each resource, and what archiving changes (#4145)."""

import pytest

from apps.experiments.models import ConsentForm, SourceMaterial
from apps.utils.factories.experiment import ChatbotFactory, ConsentFormFactory, SourceMaterialFactory
from apps.utils.factories.pipelines import NodeFactory


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
def test_source_material_in_use_lists_what_uses_it(client, team):
    material = SourceMaterialFactory.create(team=team)
    chatbot = ChatbotFactory.create(team=team)
    node = NodeFactory.create(pipeline=chatbot.pipeline, params={"source_material_id": material.id})
    published = chatbot.create_new_version(make_default=True)
    orphan = NodeFactory.create(params={"source_material_id": material.id})

    body = client.delete(f"/api/v2/source-material/{material.id}/").json()

    assert body["chatbots"] == [
        {
            "chatbot_id": str(chatbot.public_id),
            "version_number": 1,
            "published": True,
            "is_working_version": False,
            "node_ids": [published.pipeline.node_set.get(source_material__isnull=False).flow_id],
        },
        {
            "chatbot_id": str(chatbot.public_id),
            "version_number": 2,
            "published": False,
            "is_working_version": True,
            "node_ids": [node.flow_id],
        },
    ]
    assert body["other_pipelines"] == [{"name": orphan.pipeline.name}]


@pytest.mark.django_db()
def test_source_material_in_a_draft_says_to_remove_it_from_the_draft(client, team):
    material = SourceMaterialFactory.create(team=team)
    NodeFactory.create(pipeline=ChatbotFactory.create(team=team).pipeline, params={"source_material_id": material.id})

    detail = client.delete(f"/api/v2/source-material/{material.id}/").json()["detail"]

    assert "Remove it from the working version's nodes" in detail
    assert "chatbot_version_create" not in detail


@pytest.mark.django_db()
def test_source_material_a_published_version_uses_says_to_publish_and_archive_it(client, team):
    material = SourceMaterialFactory.create(team=team)
    chatbot = ChatbotFactory.create(team=team)
    node = NodeFactory.create(pipeline=chatbot.pipeline, params={"source_material_id": material.id})
    chatbot.create_new_version(make_default=True)
    node.set_params({**node.params, "source_material_id": None})

    detail = client.delete(f"/api/v2/source-material/{material.id}/").json()["detail"]

    assert "Remove it from" not in detail
    assert "`chatbot_version_create`" in detail
    assert "`chatbot_version_archive`" in detail


@pytest.mark.django_db()
def test_source_material_an_old_version_uses_is_listed_as_that_version(client, team):
    material = SourceMaterialFactory.create(team=team)
    chatbot = ChatbotFactory.create(team=team)
    node = NodeFactory.create(pipeline=chatbot.pipeline, params={"source_material_id": material.id})
    chatbot.create_new_version(make_default=True)
    node.set_params({**node.params, "source_material_id": None})
    chatbot.create_new_version(make_default=True)

    body = client.delete(f"/api/v2/source-material/{material.id}/").json()

    assert [(c["version_number"], c["published"], c["is_working_version"]) for c in body["chatbots"]] == [
        (1, False, False)
    ]
    assert body["other_pipelines"] == []
    assert "`chatbot_version_archive`" in body["detail"]
    assert client.delete(f"/api/v2/chatbots/{chatbot.public_id}/versions/1/").status_code == 200
    assert client.delete(f"/api/v2/source-material/{material.id}/").status_code == 200


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
def test_archiving_a_consent_form_leaves_published_versions_on_it(client, team):
    ConsentForm.get_default(team)
    form = ConsentFormFactory.create(team=team)
    chatbot = ChatbotFactory.create(team=team, consent_form=form)
    published = chatbot.create_new_version(make_default=True)
    published_form_id = published.consent_form_id

    response = client.delete(f"/api/v2/consent-forms/{form.id}/")

    assert response.status_code == 200, response.content
    published.refresh_from_db()
    assert published.consent_form_id == published_form_id


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
def test_created_source_material_is_owned_by_the_caller(client, team):
    response = client.post("/api/v2/source-material/", {"topic": "Returns", "material": "30 days."}, format="json")

    assert response.status_code == 201, response.content
    assert SourceMaterial.objects.get(pk=response.json()["id"]).owner == team.members.first()
