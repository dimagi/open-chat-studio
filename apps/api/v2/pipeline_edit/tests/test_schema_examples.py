"""The documented request examples are real request bodies (#4140).

``params`` is a free-form object in the schema, so these examples are the only place the API says
what a body for a given node type looks like -- documentation nothing would otherwise check.

Held to the node schemas here on all three counts that can go stale: every served type is covered,
each example names exactly the params its type declares, and each is a body the endpoint accepts.
"""

import pytest

from apps.api.v2.discovery.node_types import get_node_type_schema, get_node_types
from apps.api.v2.pipeline_edit.examples import (
    FULL_PARAMS,
    MINIMAL_CREATE,
    NOTES,
    UPDATE_BODIES,
    create_examples,
    update_examples,
)

from .conftest import node_url, nodes_url

SERVED_TYPES = [node_type["type"] for node_type in get_node_types()]

#: The params whose example values are placeholder ids, so a test that sends one has to swap in ids
#: the team holds. Not `contract.PARAMETER_OPTION_SOURCES`, which names option lists: `tools` is a
#: checked reference too, but its names are a fixed vocabulary the example already uses real ones of.
PLACEHOLDER_ID_PARAMS = frozenset(
    {
        "llm_provider_id",
        "llm_provider_model_id",
        "source_material_id",
        "collection_id",
        "collection_index_ids",
        "custom_actions",
        "synthetic_voice_id",
    }
)


class TestExampleShape:
    """What the examples say, held against the node schemas without going near the endpoint."""

    def test_every_served_type_has_an_example(self):
        assert set(FULL_PARAMS) == set(SERVED_TYPES)
        assert set(NOTES) == set(SERVED_TYPES)

    @pytest.mark.parametrize("node_type", SERVED_TYPES, ids=SERVED_TYPES)
    def test_an_example_names_exactly_the_params_its_type_declares(self, node_type):
        """Both directions matter: an example carrying a param the type does not declare documents a
        key that does nothing, and one leaving a param out sends the reader to the JSON Schema anyway.
        """
        declared = set(get_node_type_schema(node_type)["schema"]["properties"])

        assert set(FULL_PARAMS[node_type]) == declared

    def test_the_create_examples_are_named_after_their_type(self):
        """A set, not a list: the examples are ordered simplest type first so the list reads as an
        introduction, which is not the order the discovery endpoint serves them in.
        """
        examples = [example for example in create_examples() if example.name != MINIMAL_CREATE.name]

        assert {example.name for example in examples} == set(SERVED_TYPES)

    @pytest.mark.parametrize("build", [create_examples, update_examples], ids=["create", "update"])
    def test_the_examples_are_request_only(self, build):
        """drf-spectacular puts a `response_only` example under the response instead, where a request
        body would read as something the endpoint returns."""
        assert all(example.request_only for example in build())

    def test_only_the_create_examples_carry_a_type(self):
        """`type` is what POST needs and what PATCH refuses -- a node's type decides what its params
        mean, so it is fixed once the node exists."""
        assert all("type" in example.value for example in create_examples())
        assert not any("type" in example.value for example in update_examples())

    def test_the_update_examples_are_partial_bodies(self):
        """The point of publishing them rather than the create payloads again: a PATCH merges key by
        key, so a body naming every param says the opposite of what the endpoint does."""
        for body in UPDATE_BODIES:
            params = body.value.get("params", {})

            assert set(params) < set(FULL_PARAMS[body.node_type]), body.name


@pytest.mark.django_db()
class TestExamplesAreAccepted:
    """Each example sent as documented, bar the placeholder ids."""

    @pytest.fixture()
    def reference_ids(self, llm, source_material, media_collection, collection_indexes, custom_action, synthetic_voice):
        """Real ids for the params whose example values are placeholders, keyed by param name."""
        provider, model = llm
        support_kb, billing_kb = collection_indexes
        return {
            "llm_provider_id": provider.id,
            "llm_provider_model_id": model.id,
            "source_material_id": source_material.id,
            "collection_id": media_collection.id,
            "collection_index_ids": [support_kb.id, billing_kb.id],
            "custom_actions": [f"{custom_action.id}:weather_get"],
            "synthetic_voice_id": synthetic_voice.id,
        }

    @pytest.mark.parametrize("node_type", SERVED_TYPES, ids=SERVED_TYPES)
    def test_a_documented_create_example_is_accepted(self, client, chatbot, node_type, reference_ids):
        """Accepted *and* free of node errors -- a body that persists while reporting an error is a
        poor example to publish."""
        example = next(example for example in create_examples() if example.name == node_type)
        body = {**example.value, "params": _with_real_ids(example.value["params"], reference_ids)}

        response = client.post(nodes_url(chatbot), body, format="json")

        assert response.status_code == 201, response.content
        assert response.json()["pipeline_errors"]["node"] == {}

    def test_the_minimal_create_example_is_accepted(self, client, chatbot):
        response = client.post(nodes_url(chatbot), MINIMAL_CREATE.value, format="json")

        assert response.status_code == 201, response.content

    @pytest.mark.parametrize("node_type", SERVED_TYPES, ids=SERVED_TYPES)
    def test_a_types_full_param_set_is_accepted_by_patch(self, client, chatbot, node_type, reference_ids):
        """PATCHed onto a node created from its type alone, which is the state the minimal create
        example leaves one in -- so this is the second half of the loop the two examples describe.

        Driven from `FULL_PARAMS` rather than from a documented PATCH body: the schema no longer
        publishes one per type, but every param still has to be settable by the verb that exists to
        set them.
        """
        created = client.post(nodes_url(chatbot), {"type": node_type}, format="json")
        assert created.status_code == 201, created.content
        body = {"params": _with_real_ids(FULL_PARAMS[node_type], reference_ids)}

        response = client.patch(node_url(chatbot, created.json()["node"]["node_id"]), body, format="json")

        assert response.status_code == 200, response.content
        assert response.json()["pipeline_errors"]["node"] == {}

    @pytest.mark.parametrize("body", UPDATE_BODIES, ids=[body.name for body in UPDATE_BODIES])
    def test_a_documented_update_example_is_accepted(self, client, chatbot, body):
        """Sent to a node of the type it is written for, created from that type alone.

        Node errors are not asserted away here the way they are for the create examples: a one-key
        body onto a bare node leaves the type's other required params unset, which is the whole
        point of a partial body.
        """
        created = client.post(nodes_url(chatbot), {"type": body.node_type}, format="json")
        assert created.status_code == 201, created.content

        response = client.patch(node_url(chatbot, created.json()["node"]["node_id"]), body.value, format="json")

        assert response.status_code == 200, response.content


def _with_real_ids(params: dict, reference_ids: dict) -> dict:
    """``params`` with each placeholder id replaced by one the team holds, and nothing else touched.

    The assertion holds the list of params needing a substitute and the fixture supplying them to
    each other, so neither can gain an entry without the other. A reference param added to a node
    type and to neither goes unsubstituted, and fails as a 400 in whichever example test sends it.
    """
    unknown = PLACEHOLDER_ID_PARAMS.symmetric_difference(reference_ids)
    assert not unknown, f"no id to substitute for {sorted(unknown)}"
    return {name: reference_ids.get(name, value) for name, value in params.items()}
