"""The contract the discovery endpoints keep: what they serve, how they scope and fail, and how the
payload is documented.

Which of a team's resources reach the option lists is covered in
`apps/pipelines/tests/test_node_options.py`, next to the helpers that build them.
"""

import pytest
from django.urls import reverse
from rest_framework import serializers

from apps.api.v2.discovery.node_types import _property, option_keys_for_node_type
from apps.api.v2.discovery.serializers import PipelineOptionsSerializer
from apps.api.v2.discovery.views import PIPELINE_OPTIONS_EXAMPLE, PipelineOptionsView
from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import SourceMaterialFactory, SyntheticVoiceFactory
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    LlmProviderModelFactory,
    VoiceProviderFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def team_with_resources(db):
    team = TeamWithUsersFactory.create()
    LlmProviderFactory.create(team=team, type="openai", name="Prod OpenAI")
    LlmProviderModelFactory.create(team=team, type="openai")
    VoiceProviderFactory.create(team=team, name="Prod Polly")
    SourceMaterialFactory.create(team=team, topic="Returns policy")
    # Pinned to None so CollectionFactory's SubFactories don't add an LlmProvider to this team.
    CollectionFactory.create(
        team=team, name="Policy docs", is_index=False, llm_provider=None, embedding_provider_model=None
    )
    return team


@pytest.fixture()
def team_with_every_resource(team_with_resources):
    """One entry in every option list, so a shape assertion has something to look at in each."""
    team = team_with_resources
    CollectionFactory.create(
        team=team,
        name="Support KB",
        is_index=True,
        is_remote_index=True,
        llm_provider=None,
        embedding_provider_model=None,
    )
    CustomActionFactory.create(team=team, name="Orders API")
    # One voice tied to the team's provider and one of the shared AWS voices, which carry no provider.
    SyntheticVoiceFactory.create(name="Joanna", service="AWS", voice_provider=team.voiceprovider_set.first())
    SyntheticVoiceFactory.create(name="Matthew", service="AWS")
    return team


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_list_node_types(auth_method, team):
    """Every listed type has to arrive complete: a client picks one off this response and builds from
    what came with it, so a blank description or an empty `properties` leaves it nothing to go on."""
    client = ApiTestClient(team.members.first(), team, auth_method=auth_method)
    response = client.get(reverse("api:v2:pipeline-nodes"))

    assert response.status_code == 200
    entries = response.json()
    assert "LLMResponseWithPrompt" in {entry["type"] for entry in entries}
    for entry in entries:
        assert entry["description"].strip(), entry["type"]
        assert entry["schema"]["properties"], entry["type"]


@pytest.mark.django_db()
def test_unbuildable_node_types_are_excluded(team):
    """Deprecated types and the structural ones the server manages are left out entirely, and
    `can_add` -- the builder's own reason for hiding them -- is not served as a flag to interpret."""
    client = ApiTestClient(team.members.first(), team)
    entries = client.get(reverse("api:v2:pipeline-nodes")).json()

    types = {entry["type"] for entry in entries}
    assert {"AssistantNode", "BooleanNode", "LLMResponse"}.isdisjoint(types)
    assert {"StartNode", "EndNode", "Passthrough"}.isdisjoint(types)
    assert "LLMResponseWithPrompt" in types
    assert not [entry for entry in entries if "can_add" in entry]


@pytest.mark.django_db()
def test_no_namespaced_schema_key_survives_anywhere(team):
    """The builder's schemas carry `ui:`/`api:` keys. The few that mean anything to a client are
    re-served under a plain name, so a namespaced key reaching the response is an untranslated one --
    at the schema level or on any single param."""
    client = ApiTestClient(team.members.first(), team)
    for entry in client.get(reverse("api:v2:pipeline-nodes")).json():
        assert not [key for key in entry["schema"] if ":" in key], entry["type"]
        for name, prop in entry["schema"]["properties"].items():
            assert not [key for key in prop if ":" in key], f"{entry['type']}.{name}"


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("node_type", "expected_keys"),
    [
        pytest.param(
            "LLMResponseWithPrompt",
            {"llm_provider_id", "llm_provider_model_id", "tool_config", "synthetic_voice_id"},
            id="params-rendered-with-a-bespoke-widget",
        ),
        pytest.param(
            "LLMResponseWithPrompt",
            {"source_material", "collection", "collection_index", "tools", "custom_actions"},
            id="params-the-builder-declares-a-source-for",
        ),
        pytest.param("RenderTemplate", {"template_variables"}, id="template-vars"),
        pytest.param("SendEmail", {"template_variables"}, id="template-vars-outside-the-template-node"),
    ],
)
def test_scoping_covers_every_param_that_reads_an_option_list(team_with_resources, node_type, expected_keys):
    """The scoped endpoint derives its payload from `ui:optionsSource`, so a param missing the
    declaration leaves the client unable to fill it from the scoped response."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    scoped = client.get(reverse("api:v2:pipeline-node-options", args=[node_type])).json()

    assert expected_keys <= set(scoped)


@pytest.mark.django_db()
def test_every_option_key_is_named_for_the_param_that_reads_it(team_with_resources):
    """The payload's central promise, and the only thing tying a list to a param: `_property()` strips
    `ui:optionsSource`, so a client has nothing but the name -- give or take an `_id`/`_ids` suffix --
    to match them up. The variable lists have no param named for them and are documented as the
    exception; `default_llm_provider` is a starting pair for two params rather than a list of its own."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    params = {
        name for entry in client.get(reverse("api:v2:pipeline-nodes")).json() for name in entry["schema"]["properties"]
    }
    option_keys = set(client.get(reverse("api:v2:pipeline-options")).json())

    unmatched = {key for key in option_keys if not {key, f"{key}_id", f"{key}_ids"} & params}

    assert unmatched == {
        "template_variables",
        "llm_prompt_variables",
        "router_prompt_variables",
        "default_llm_provider",
    }


@pytest.mark.django_db()
def test_every_key_a_node_type_scopes_to_is_actually_served(team_with_resources):
    """Scoping drops a key the payload doesn't carry silently rather than failing."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    option_keys = set(client.get(reverse("api:v2:pipeline-options")).json())

    dangling = {}
    for entry in client.get(reverse("api:v2:pipeline-nodes")).json():
        scoped_keys = option_keys_for_node_type(entry["type"])
        assert scoped_keys is not None, f"{entry['type']} is listed but cannot be scoped to"
        dangling[entry["type"]] = sorted(scoped_keys - option_keys)
    assert not {node_type: keys for node_type, keys in dangling.items() if keys}


@pytest.mark.django_db()
def test_every_key_served_is_read_by_some_listed_node_type(team_with_resources):
    """The other half of the promise above: a key no param can reach is one a client has nothing to
    write into, so the unscoped payload holds no more than the scoped ones add up to."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    option_keys = set(client.get(reverse("api:v2:pipeline-options")).json())

    read = set()
    for entry in client.get(reverse("api:v2:pipeline-nodes")).json():
        scoped_keys = option_keys_for_node_type(entry["type"])
        assert scoped_keys is not None, f"{entry['type']} is listed but cannot be scoped to"
        read |= scoped_keys

    assert not option_keys - read


@pytest.mark.django_db()
def test_conditional_params_declare_when_they_apply(team):
    """`ui:visibleWhen` is a rendering rule for the builder. A client draws no forms, so it is served
    the same condition as the answer to "is this param read at all?"."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    token_limit = by_type["LLMResponseWithPrompt"]["schema"]["properties"]["user_max_token_limit"]
    assert token_limit["applies_when"] == {
        "field": "history_mode",
        "operator": "in",
        "value": ["summarize", "truncate_tokens"],
    }


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("node_type", "expected"),
    [
        pytest.param(
            "RouterNode",
            {"kind": "per_keyword", "handle_pattern": "output_{index}"},
            id="router-has-one-output-per-keyword",
        ),
        pytest.param(
            "StaticRouterNode",
            {"kind": "per_keyword", "handle_pattern": "output_{index}"},
            id="static-router-has-one-output-per-keyword",
        ),
        pytest.param("LLMResponseWithPrompt", {"kind": "single", "handles": ["output"]}, id="plain-node"),
        pytest.param("CodeNode", {"kind": "single", "handles": ["output"]}, id="another-plain-node"),
    ],
)
def test_node_types_declare_their_output_topology(team, node_type, expected):
    """Every edge needs a `source_handle`, and a router's handles depend on its own params, so the
    client is told either the handle names or the pattern to build them from."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    outputs = by_type[node_type]["outputs"]
    assert {key: outputs[key] for key in expected} == expected
    assert outputs["description"].strip()


@pytest.mark.django_db()
def test_documented_node_types_expose_an_absolute_documentation_url(team):
    """The stored `ui:documentation_link` is site-relative; a client has no base to join it to."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    assert by_type["LLMResponseWithPrompt"]["documentation_url"].startswith("http")


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "node_type",
    [
        pytest.param("LLMResponseWithPrompt", id="llm-node"),
        pytest.param("RouterNode", id="router-node"),
        pytest.param("ExtractStructuredData", id="extract-node"),
    ],
)
def test_the_model_must_match_its_provider(team, node_type):
    """The builder enforces this pairing in JS and the schema never stated it, so every node type
    taking both a provider and a model has to carry the rule."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    model_id = by_type[node_type]["schema"]["properties"]["llm_provider_model_id"]
    assert model_id["must_match"] == {"field": "llm_provider_id", "on": "type"}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "param",
    [
        pytest.param("built_in_tools", id="built-in-tools"),
        pytest.param("tool_config", id="tool-config"),
    ],
)
def test_provider_keyed_params_say_what_keys_them(team, param):
    """Both are dicts keyed by provider type rather than flat option lists."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    prop = by_type["LLMResponseWithPrompt"]["schema"]["properties"][param]
    assert prop["options_keyed_by"] == {"field": "llm_provider_id", "on": "type"}


def test_feature_flagged_params_are_marked():
    """Tested against the translation directly: no served param carries `ui:flagRequired` today."""
    assert _property("some_param", {"type": "array", "ui:flagRequired": "flag_x"}) == {
        "type": "array",
        "requires_feature_flag": "flag_x",
    }


@pytest.mark.django_db()
def test_a_withheld_param_reaches_neither_endpoint(team_with_resources):
    """`UiSchema(api_exclude=True)` withholds the param and its option list together."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    for entry in client.get(reverse("api:v2:pipeline-nodes")).json():
        schema = entry["schema"]
        assert "mcp_tools" not in schema["properties"], entry["type"]
        assert "mcp_tools" not in schema.get("required", []), entry["type"]

    assert "mcp_tools" not in client.get(reverse("api:v2:pipeline-options")).json()


@pytest.mark.django_db()
def test_one_node_type_is_retrievable_by_name(team):
    """The detail endpoint serves the same entry the list holds, as a bare object."""
    client = ApiTestClient(team.members.first(), team)
    response = client.get(reverse("api:v2:pipeline-node", args=["RouterNode"]))

    assert response.status_code == 200
    assert response.json() == next(
        entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json() if entry["type"] == "RouterNode"
    )


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "node_type",
    [
        pytest.param("Frobnicator", id="unknown-type"),
        pytest.param("BooleanNode", id="deprecated-type-is-not-discoverable"),
        pytest.param("AssistantNode", id="deprecated-type-whose-builder-advice-is-markup"),
    ],
)
def test_node_detail_404s(team, node_type):
    """A name that was never a node type and one that is no longer buildable get the same answer:
    neither is something a client may build, and the body carries no builder markup either way."""
    client = ApiTestClient(team.members.first(), team)
    response = client.get(reverse("api:v2:pipeline-node", args=[node_type]))

    assert response.status_code == 404
    assert response.json()["detail"] == f"Unknown node type: {node_type}"
    assert sorted(response.json()) == ["detail", "valid_types"]


@pytest.mark.django_db()
def test_404_body_lists_the_types_the_client_could_have_asked_for(team):
    """`valid_types` is exactly the unfiltered list, so a failed call can be corrected from its own
    error body rather than a second request."""
    client = ApiTestClient(team.members.first(), team)
    body = client.get(reverse("api:v2:pipeline-node", args=["Frobnicator"])).json()

    listed = {entry["type"] for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}
    assert set(body["valid_types"]) == listed


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "node_type",
    [
        pytest.param("StartNode", id="start"),
        pytest.param("EndNode", id="end"),
        pytest.param("Passthrough", id="passthrough"),
    ],
)
def test_structural_type_is_reported_as_server_managed_not_unknown(team, node_type):
    """These are unlisted, but `/inspect/` still reports them as the `type` of real nodes."""
    client = ApiTestClient(team.members.first(), team)
    response = client.get(reverse("api:v2:pipeline-node", args=[node_type]))

    assert response.status_code == 404
    assert "managed by the server" in response.json()["detail"]
    assert "Unknown" not in response.json()["detail"]


@pytest.mark.django_db()
def test_node_list_is_revalidatable(team):
    """The list is static per deploy, so a client holding a current copy is answered 304 and no body
    rather than the payload again."""
    client = ApiTestClient(team.members.first(), team)
    response = client.get(reverse("api:v2:pipeline-nodes"))
    etag = response.headers["ETag"]

    revalidated = client.get(reverse("api:v2:pipeline-nodes"), HTTP_IF_NONE_MATCH=etag)

    assert revalidated.status_code == 304
    assert not revalidated.content


@pytest.mark.django_db()
def test_node_detail_is_revalidatable(team):
    """One node type is as static as the whole list, and carries its own ETag -- one covering both
    would let a detail request revalidate against the full list."""
    client = ApiTestClient(team.members.first(), team)
    url = reverse("api:v2:pipeline-node", args=["RouterNode"])
    response = client.get(url)
    etag = response.headers["ETag"]

    assert etag != client.get(reverse("api:v2:pipeline-nodes")).headers["ETag"]

    revalidated = client.get(url, HTTP_IF_NONE_MATCH=etag)

    assert revalidated.status_code == 304
    assert not revalidated.content


@pytest.mark.django_db()
def test_unauthenticated_request_is_rejected(team, client):
    """Every endpoint answers for the caller's team, so none serves an anonymous request."""
    assert client.get(reverse("api:v2:pipeline-nodes")).status_code == 401
    assert client.get(reverse("api:v2:pipeline-node", args=["RouterNode"])).status_code == 401
    assert client.get(reverse("api:v2:pipeline-options")).status_code == 401
    assert client.get(reverse("api:v2:pipeline-node-options", args=["RouterNode"])).status_code == 401


@pytest.mark.django_db()
def test_read_only_api_key_may_read(team):
    """Discovery is a read: a key with no write access still gets it."""
    client = ApiTestClient(team.members.first(), team, read_only=True)
    assert client.get(reverse("api:v2:pipeline-nodes")).status_code == 200


@pytest.mark.django_db()
def test_options_carry_no_placeholder_entries(team_with_resources):
    """The builder emits `{"value": "", "label": "Select a topic"}`; an empty id is useless here."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    options = client.get(reverse("api:v2:pipeline-options")).json()

    for key, value in options.items():
        if isinstance(value, list):
            assert all(option.get("value") != "" for option in value if isinstance(option, dict)), key


@pytest.mark.django_db()
def test_options_carry_no_edit_urls(team_with_resources):
    """`edit_url` links into the Django UI, which an API client has no way to follow."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    assert "edit_url" not in client.get(reverse("api:v2:pipeline-options")).content.decode()


def test_clean_options_recurses_into_nested_dicts():
    """`built_in_tools` nests lists one level down and `tool_config` two, so the strip has to recurse.
    `tool_config` entries have no `value` key, so the placeholder strip must leave them alone."""
    nested = {
        "built_in_tools": {
            "openai": [
                {"value": "web-search", "label": "Web Search", "edit_url": "/tools/web-search"},
                {"value": "", "label": "Select a tool"},
            ],
        },
        "tool_config": {
            "anthropic": {
                "web-search": [
                    {
                        "name": "allowed_domains",
                        "type": "expandable_text",
                        "label": "Allowed Domains",
                        "helpText": "Only search these domains. Separate entries with newlines.",
                        "edit_url": "/tools/anthropic/web-search",
                    },
                    {
                        "name": "blocked_domains",
                        "type": "expandable_text",
                        "label": "Blocked Domains",
                        "helpText": "Exclude these domains from search. Separate entries with newlines.",
                    },
                ],
            },
        },
    }

    cleaned = PipelineOptionsView._clean_options(nested)

    assert cleaned["built_in_tools"]["openai"] == [{"value": "web-search", "label": "Web Search"}]
    assert cleaned["tool_config"]["anthropic"]["web-search"] == [
        {
            "name": "allowed_domains",
            "type": "expandable_text",
            "label": "Allowed Domains",
            "helpText": "Only search these domains. Separate entries with newlines.",
        },
        {
            "name": "blocked_domains",
            "type": "expandable_text",
            "label": "Blocked Domains",
            "helpText": "Exclude these domains from search. Separate entries with newlines.",
        },
    ]


@pytest.mark.django_db()
def test_options_can_be_scoped_to_one_node_type(team_with_resources):
    """`/pipeline/options/{node_type}/` cuts the payload down to the keys that node type's params can
    read."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    scoped = client.get(reverse("api:v2:pipeline-node-options", args=["RenderTemplate"])).json()

    assert set(scoped) == {"template_variables"}


@pytest.mark.django_db()
def test_scoped_options_keep_the_provider_defaults_for_llm_nodes(team_with_resources):
    """A scoped response still has to be enough to build the node on its own."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    scoped = client.get(reverse("api:v2:pipeline-node-options", args=["LLMResponseWithPrompt"])).json()

    assert "default_llm_provider" in scoped
    assert "llm_provider_id" in scoped
    assert "template_variables" not in scoped


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("node_type", "expected_detail"),
    [
        pytest.param("Frobnicator", "Unknown node type", id="unknown-type"),
        pytest.param("StartNode", "managed by the server", id="server-managed-type"),
    ],
)
def test_scoped_options_404_like_the_node_detail(team_with_resources, node_type, expected_detail):
    """A client walks both endpoints with the same type name in the path, so an unusable name has to
    fail the same way on each -- body included."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    response = client.get(reverse("api:v2:pipeline-node-options", args=[node_type]))

    assert response.status_code == 404
    assert expected_detail in response.json()["detail"]
    assert sorted(response.json()) == ["detail", "valid_types"]


@pytest.mark.django_db()
def test_a_node_type_that_references_nothing_scopes_to_an_empty_object(team_with_resources):
    """`CodeNode` is addable and every param is free text, so it scopes to nothing -- which is a
    different answer from "no such type"."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    response = client.get(reverse("api:v2:pipeline-node-options", args=["CodeNode"]))

    assert response.status_code == 200
    assert response.json() == {}


def test_the_documented_example_carries_every_key_the_serializer_declares():
    """A reader takes the response sample for the whole payload, so a key the sample omits reads as
    a key the endpoint doesn't serve."""
    assert list(PIPELINE_OPTIONS_EXAMPLE) == list(PipelineOptionsSerializer().fields)


def _documented_option_shapes():
    """Every key holding a list of options, and the serializer documenting one entry of it."""
    shapes = {}
    for key, field in PipelineOptionsSerializer().fields.items():
        if isinstance(field, serializers.DictField):
            field = field.child  # `built_in_tools` nests its lists under the provider type
        entry = getattr(field, "child", None)
        if isinstance(entry, serializers.Serializer):
            shapes[key] = entry
    return shapes


OPTION_LIST_KEYS = sorted(_documented_option_shapes())
VALUE_BEARING_OPTION_KEYS = sorted(key for key, shape in _documented_option_shapes().items() if "value" in shape.fields)


def _served_entries(served):
    """The entries under one option key, with the provider-keyed dicts flattened into one list."""
    if isinstance(served, dict):
        return [entry for group in served.values() for entry in group]
    return served


@pytest.mark.django_db()
@pytest.mark.parametrize("key", OPTION_LIST_KEYS)
def test_every_option_list_documents_the_fields_it_serves(team_with_every_resource, key):
    """The lists share no single option shape -- `type` belongs to the provider-backed ones,
    `provider_id` to `synthetic_voice_id`, `max_token_limit` to `llm_provider_model_id` -- so one
    shape documented for all of them puts fields on lists that will never carry them."""
    documented = _documented_option_shapes()[key]
    client = ApiTestClient(team_with_every_resource.members.first(), team_with_every_resource)

    entries = _served_entries(client.get(reverse("api:v2:pipeline-options")).json()[key])

    assert entries, f"{key} came back empty, so it holds the docs to nothing"
    required = {name for name, field in documented.fields.items() if field.required}
    assert {name for entry in entries for name in entry} == set(documented.fields)
    assert all(required <= set(entry) for entry in entries), entries


@pytest.mark.django_db()
@pytest.mark.parametrize("key", VALUE_BEARING_OPTION_KEYS)
def test_every_option_value_has_the_documented_type(team_with_every_resource, key):
    """`value` is written straight into a node param, so an id documented as an integer and served as
    a string is a break even though both are JSON scalars."""
    documented_value = _documented_option_shapes()[key].fields["value"]
    expected_type = {serializers.IntegerField: int, serializers.CharField: str}[type(documented_value)]
    client = ApiTestClient(team_with_every_resource.members.first(), team_with_every_resource)

    entries = _served_entries(client.get(reverse("api:v2:pipeline-options")).json()[key])

    assert entries, f"{key} came back empty, so it holds the docs to nothing"
    assert all(isinstance(entry["value"], expected_type) for entry in entries), entries


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("node_type", "option_key", "expected_vars"),
    [
        pytest.param(
            "LLMResponseWithPrompt",
            "llm_prompt_variables",
            {
                "participant_data",
                "source_material",
                "current_datetime",
                "media",
                "collection_index_summaries",
                "temp_state",
                "session_state",
            },
            id="llm-node",
        ),
        pytest.param(
            "RouterNode",
            "router_prompt_variables",
            {"temp_state", "participant_data", "session_state"},
            id="router-node",
        ),
        pytest.param(
            "RenderTemplate",
            "template_variables",
            {
                "input",
                "node_inputs",
                "temp_state",
                "session_state",
                "participant_data",
                "participant_details",
                "participant_schedules",
                "input_message_id",
                "input_message_url",
            },
            id="template-node",
        ),
    ],
)
def test_each_variable_flavour_serves_its_own_set(team_with_resources, node_type, option_key, expected_vars):
    """`prompt` on an LLM node takes `source_material` and `media`, `prompt` on a router takes
    neither, and the Jinja params take the node-graph variables instead."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    scoped = client.get(reverse("api:v2:pipeline-options"), {"node_type": node_type}).json()

    assert option_key in scoped, f"{node_type} must be able to resolve its variables"
    assert {entry["label"] for entry in scoped[option_key]} == expected_vars
    for entry in scoped[option_key]:
        assert sorted(entry) == ["description", "label"], entry
        assert entry["description"].strip()
