"""The corners the façade has to answer for rather than crash on (#4140)."""

from unittest.mock import Mock, patch

import pytest

from apps.pipelines.models import Node

from .conftest import node_url, nodes_url


@pytest.mark.django_db()
def test_a_label_only_edit_to_a_deprecated_type_is_allowed(client, chatbot):
    """The API cannot describe a deprecated type's params, so it refuses to *set* them -- but
    renaming such a node needs no schema, and a pipeline holding one has to stay editable."""
    node = Node.objects.create(
        pipeline=chatbot.pipeline, type="LLMResponse", flow_id="LLMResponse-old1", label="Old", params={"name": "old"}
    )
    chatbot.pipeline.data["nodes"] = []
    chatbot.pipeline.save(update_fields=["data"])

    response = client.patch(node_url(chatbot, node.flow_id), {"label": "Renamed"}, format="json")

    assert response.status_code == 200, response.content
    node.refresh_from_db()
    assert node.label == "Renamed"


@pytest.mark.django_db()
def test_setting_a_param_on_a_deprecated_type_is_refused(client, chatbot):
    """The other half of it: the API has no schema to check the value against, so it will not
    pretend to."""
    node = Node.objects.create(
        pipeline=chatbot.pipeline, type="LLMResponse", flow_id="LLMResponse-old2", label="Old", params={"name": "old"}
    )
    chatbot.pipeline.data["nodes"] = []
    chatbot.pipeline.save(update_fields=["data"])

    response = client.patch(node_url(chatbot, node.flow_id), {"params": {"name": "new"}}, format="json")

    assert response.status_code == 404, response.content


@pytest.mark.django_db()
def test_a_colliding_node_id_is_redrawn(client, chatbot):
    """``apply_pipeline_patch`` treats an add whose id already exists as a no-op, so a clash would
    answer 201 while describing the node that was already there."""
    created = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")
    taken = created.json()["node"]["node_id"]
    collision = taken.removeprefix("CodeNode-")

    # The first draw repeats the id already in the graph, the second is free.
    draws = [Mock(hex=f"{collision}0000000"), Mock(hex="abcde" + "0" * 27)]
    with patch("apps.api.v2.pipeline_edit.graph_editor.uuid4", side_effect=draws):
        response = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")

    assert response.status_code == 201, response.content
    assert response.json()["node"]["node_id"] == "CodeNode-abcde"
    assert Node.objects.filter(pipeline=chatbot.pipeline, type="CodeNode").count() == 2


@pytest.mark.django_db()
def test_an_id_source_that_only_collides_still_answers(client, chatbot):
    """The redraw is bounded: an id source stuck on one value must fall back to a longer id rather
    than spin inside the pipeline row lock until the request times out."""
    created = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")
    collision = created.json()["node"]["node_id"].removeprefix("CodeNode-")

    with patch("apps.api.v2.pipeline_edit.graph_editor.uuid4", return_value=Mock(hex=f"{collision}{'0' * 27}")):
        response = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")

    assert response.status_code == 201, response.content
    assert response.json()["node"]["node_id"] == f"CodeNode-{collision}{'0' * 27}"
    assert Node.objects.filter(pipeline=chatbot.pipeline, type="CodeNode").count() == 2


@pytest.mark.django_db()
def test_options_on_the_detail_route_describes_the_patch_body(client, chatbot):
    """OPTIONS is how the agent this API is built for discovers what it may send. PATCH is the only
    writable verb on this route, and stock DRF metadata describes PUT and POST alone -- so without
    the metadata class the answer carries no body at all."""
    node_id = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json").json()["node"]["node_id"]

    response = client.options(node_url(chatbot, node_id))

    assert response.status_code == 200, response.content
    assert set(response.json()["actions"]["PATCH"]) == {"label", "params"}


@pytest.mark.django_db()
def test_options_on_the_collection_route_describes_the_post_body(client, chatbot):
    """One view serves both routes, so the body described has to be resolved per verb, not per
    class -- otherwise this would advertise the *edit* body, missing `type`, which POST requires."""
    response = client.options(nodes_url(chatbot))

    assert response.status_code == 200, response.content
    assert set(response.json()["actions"]["POST"]) == {"type", "label", "params"}


@pytest.mark.django_db()
def test_a_verb_the_route_does_not_offer_is_a_405(client, chatbot):
    """Each `path()` narrows `http_method_names` to the verbs its own route offers -- without that,
    a verb from the other route reaches its handler with the wrong path kwargs and raises a 500."""
    node_id = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json").json()["node"]["node_id"]

    assert client.patch(nodes_url(chatbot), {"params": {}}, format="json").status_code == 405
    assert client.delete(nodes_url(chatbot)).status_code == 405
    assert client.post(node_url(chatbot, node_id), {"type": "CodeNode"}, format="json").status_code == 405


@pytest.mark.django_db()
def test_a_write_to_an_archived_pipeline_is_a_404(client, chatbot):
    """The default manager hides archived rows, so reaching for one by pk is a ``DoesNotExist``
    rather than a miss -- a 404 is the answer, not a 500."""
    chatbot.pipeline.is_archived = True
    chatbot.pipeline.save(update_fields=["is_archived"])

    response = client.post(nodes_url(chatbot), {"type": "CodeNode"}, format="json")

    assert response.status_code == 404, response.content


@pytest.mark.django_db()
def test_an_unparseable_stored_node_is_reported_rather_than_raised(client, chatbot):
    """What makes storing an unparseable param safe at all: `code` reaches a ``mode="before"``
    validator that regex-searches it and raises ``TypeError``, which pydantic does not wrap. Reading
    the pipeline back has to report that rather than take the endpoint down -- for a node from any
    source, an import or migration as much as a façade write."""
    Node.objects.create(
        pipeline=chatbot.pipeline, type="CodeNode", flow_id="CodeNode-bad01", label="Bad", params={"code": 123}
    )
    chatbot.pipeline.data["nodes"] = []
    chatbot.pipeline.save(update_fields=["data"])

    response = client.get(f"/api/v2/chatbots/{chatbot.public_id}/inspect/")

    assert response.status_code == 200, response.content
    assert "TypeError" in response.json()["pipeline_errors"]["node"]["CodeNode-bad01"]["root"]
