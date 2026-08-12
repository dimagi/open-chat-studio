import pytest
from django.urls import reverse

from apps.api.v2.discovery.node_types import _property, option_keys_for_node_type
from apps.api.v2.discovery.serializers import PipelineOptionsSerializer
from apps.api.v2.discovery.views import PIPELINE_OPTIONS_EXAMPLE, PipelineOptionsView
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


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_list_node_types(auth_method, team):
    client = ApiTestClient(team.members.first(), team, auth_method=auth_method)
    response = client.get(reverse("api:v2:pipeline-nodes"))

    assert response.status_code == 200
    by_type = {entry["type"]: entry for entry in response.json()}
    assert by_type["LLMResponseWithPrompt"]["description"]
    assert by_type["LLMResponseWithPrompt"]["schema"]["properties"]


@pytest.mark.django_db()
def test_unbuildable_node_types_are_excluded(team):
    client = ApiTestClient(team.members.first(), team)
    entries = client.get(reverse("api:v2:pipeline-nodes")).json()

    types = {entry["type"] for entry in entries}
    assert {"AssistantNode", "BooleanNode", "LLMResponse"}.isdisjoint(types)
    assert {"StartNode", "EndNode", "Passthrough"}.isdisjoint(types)
    assert "LLMResponseWithPrompt" in types
    assert not [entry for entry in entries if "can_add" in entry]


@pytest.mark.django_db()
def test_no_namespaced_schema_key_survives_anywhere(team):
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
            {"source_material", "collection", "collection_index", "agent_tools", "custom_actions"},
            id="params-the-builder-declares-a-source-for",
        ),
        pytest.param("RenderTemplate", {"template_variables"}, id="template-vars"),
        pytest.param("SendEmail", {"template_variables"}, id="template-vars-outside-the-template-node"),
    ],
)
def test_scoping_covers_every_param_that_reads_an_option_list(team_with_resources, node_type, expected_keys):
    """`?node_type=` derives its payload from `ui:optionsSource`, so a param missing the declaration
    leaves the client unable to fill it from the scoped response."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    scoped = client.get(reverse("api:v2:pipeline-options"), {"node_type": node_type}).json()

    assert expected_keys <= set(scoped)


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
    """Pins the size of `API_ONLY_OPTION_KEYS` -- every other key must be reachable from a param."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    option_keys = set(client.get(reverse("api:v2:pipeline-options")).json())

    read = set()
    for entry in client.get(reverse("api:v2:pipeline-nodes")).json():
        scoped_keys = option_keys_for_node_type(entry["type"])
        assert scoped_keys is not None, f"{entry['type']} is listed but cannot be scoped to"
        read |= scoped_keys

    assert option_keys - read == {"voice_provider_id"}


@pytest.mark.django_db()
def test_conditional_params_declare_when_they_apply(team):
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
def test_type_filter_returns_a_single_element_array(team):
    client = ApiTestClient(team.members.first(), team)
    response = client.get(reverse("api:v2:pipeline-nodes"), {"type": "RouterNode"})

    assert response.status_code == 200
    assert [entry["type"] for entry in response.json()] == ["RouterNode"]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "node_type",
    [
        pytest.param("Frobnicator", id="unknown-type"),
        pytest.param("BooleanNode", id="deprecated-type-is-not-discoverable"),
        pytest.param("AssistantNode", id="deprecated-type-whose-builder-advice-is-markup"),
    ],
)
def test_type_filter_404s(team, node_type):
    client = ApiTestClient(team.members.first(), team)
    response = client.get(reverse("api:v2:pipeline-nodes"), {"type": node_type})

    assert response.status_code == 404
    assert response.json()["detail"] == f"Unknown node type: {node_type}"
    assert sorted(response.json()) == ["detail", "valid_types"]


@pytest.mark.django_db()
def test_404_body_lists_the_types_the_client_could_have_asked_for(team):
    client = ApiTestClient(team.members.first(), team)
    body = client.get(reverse("api:v2:pipeline-nodes"), {"type": "Frobnicator"}).json()

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
    response = client.get(reverse("api:v2:pipeline-nodes"), {"type": node_type})

    assert response.status_code == 404
    assert "managed by the server" in response.json()["detail"]
    assert "Unknown" not in response.json()["detail"]


@pytest.mark.django_db()
def test_node_list_is_revalidatable(team):
    client = ApiTestClient(team.members.first(), team)
    response = client.get(reverse("api:v2:pipeline-nodes"))
    etag = response.headers["ETag"]

    revalidated = client.get(reverse("api:v2:pipeline-nodes"), HTTP_IF_NONE_MATCH=etag)

    assert revalidated.status_code == 304
    assert not revalidated.content


@pytest.mark.django_db()
def test_etag_distinguishes_the_filtered_response(team):
    client = ApiTestClient(team.members.first(), team)
    full = client.get(reverse("api:v2:pipeline-nodes")).headers["ETag"]

    filtered = client.get(reverse("api:v2:pipeline-nodes"), {"type": "RouterNode"})

    assert filtered.headers["ETag"] != full


@pytest.mark.django_db()
def test_unauthenticated_request_is_rejected(team, client):
    assert client.get(reverse("api:v2:pipeline-nodes")).status_code == 401
    assert client.get(reverse("api:v2:pipeline-options")).status_code == 401


@pytest.mark.django_db()
def test_read_only_api_key_may_read(team):
    client = ApiTestClient(team.members.first(), team, read_only=True)
    assert client.get(reverse("api:v2:pipeline-nodes")).status_code == 200


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_options_lists_team_resources(auth_method, team_with_resources):
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources, auth_method=auth_method)
    response = client.get(reverse("api:v2:pipeline-options"))

    assert response.status_code == 200
    options = response.json()
    assert [option["label"] for option in options["llm_provider_id"]] == ["Prod OpenAI"]
    assert [option["label"] for option in options["source_material"]] == ["Returns policy"]
    assert [option["label"] for option in options["collection"]] == ["Policy docs"]


@pytest.mark.django_db()
def test_options_are_team_scoped(team_with_resources):
    other_team = TeamWithUsersFactory.create()
    LlmProviderFactory.create(team=other_team, name="Their OpenAI")
    VoiceProviderFactory.create(team=other_team, name="Their Polly")
    SourceMaterialFactory.create(team=other_team, topic="Their policy")
    CollectionFactory.create(
        team=other_team, name="Their docs", is_index=False, llm_provider=None, embedding_provider_model=None
    )

    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    options = client.get(reverse("api:v2:pipeline-options")).json()

    labels = {
        label
        for key in ("llm_provider_id", "voice_provider_id", "source_material", "collection")
        for label in (option["label"] for option in options[key])
    }
    assert not {"Their OpenAI", "Their Polly", "Their policy", "Their docs"} & labels


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
def test_options_include_voice_providers_with_type(team_with_resources):
    """`type` is the join key for the voice-pairing rule on the chatbot settings endpoint."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    voice_providers = client.get(reverse("api:v2:pipeline-options")).json()["voice_provider_id"]

    assert [option["label"] for option in voice_providers] == ["Prod Polly"]
    assert voice_providers[0]["type"] == "aws"


@pytest.mark.django_db()
def test_options_synthetic_voices_are_filtered_by_team_voice_provider_type(team_with_resources):
    """`SyntheticVoice.service` ("AWS") and `VoiceProviderType` ("aws") differ only in case, so a
    naive `service__in` match would return nothing. Azure and OpenAI voices reach every team via
    `get_for_team`, so only the provider filter excludes them."""
    SyntheticVoiceFactory.create(name="Aria", service="AWS")
    SyntheticVoiceFactory.create(name="Elan", service="Azure")
    SyntheticVoiceFactory.create(name="Coral", service="OpenAI")

    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    voices = client.get(reverse("api:v2:pipeline-options")).json()["synthetic_voice_id"]

    services = {voice["type"] for voice in voices}
    assert "aws" in services
    assert not {"azure", "openai"} & services


@pytest.mark.django_db()
def test_options_synthetic_voices_are_empty_without_a_voice_provider(team):
    SyntheticVoiceFactory.create(service="AWS")
    SyntheticVoiceFactory.create(service="Azure")

    client = ApiTestClient(team.members.first(), team)
    options = client.get(reverse("api:v2:pipeline-options")).json()

    assert options["synthetic_voice_id"] == []


@pytest.mark.django_db()
def test_options_llm_models_are_filtered_by_team_provider_type(team_with_resources):
    """Global models (`team=None`) reach every team, so without the provider filter a team holding
    only an OpenAI key is offered the ~60 models it has no provider to call. Every configured type
    survives, not just the first -- a team can hold providers for several."""
    # team_with_resources already has an openai provider, so lets add another one
    LlmProviderFactory.create(team=team_with_resources, type="anthropic", name="Prod Anthropic")
    LlmProviderModelFactory.create(team=None, type="anthropic", name="claude-sonnet-5")
    LlmProviderModelFactory.create(team=None, type="google", name="gemini-3-pro")
    LlmProviderModelFactory.create(team=None, type="openai", name="gpt-5.1")

    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    models = client.get(reverse("api:v2:pipeline-options")).json()["llm_provider_model_id"]

    assert {model["type"] for model in models} == {"openai", "anthropic"}


@pytest.mark.django_db()
def test_options_omit_deprecated_llm_models(team_with_resources):
    """Absent rather than flagged, the same as a deprecated node type. The builder still shows these
    so an existing node keeps rendering; this list is only what a client may build with."""
    LlmProviderModelFactory.create(team=None, type="openai", name="gpt-3.5-turbo", deprecated=True)

    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    models = client.get(reverse("api:v2:pipeline-options")).json()["llm_provider_model_id"]

    assert models
    assert not [model for model in models if "gpt-3.5-turbo" in model["label"]]


@pytest.mark.django_db()
def test_options_tool_config_is_scoped_to_the_teams_provider_types(team_with_resources):
    """`tool_config` is a hardcoded dict covering every provider type, unlike `built_in_tools`, which
    is built from the team's providers. Both are keyed by `llm_provider_id.type`, so both are scoped.
    Anthropic is the only type carrying configs, so it is what the two cases are told apart by."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    openai_only = client.get(reverse("api:v2:pipeline-options")).json()

    assert set(openai_only["built_in_tools"]) == {"openai"}
    assert "anthropic" not in openai_only["tool_config"]

    LlmProviderFactory.create(team=team_with_resources, type="anthropic", name="Prod Anthropic")
    with_anthropic = client.get(reverse("api:v2:pipeline-options")).json()

    assert "anthropic" in with_anthropic["tool_config"]


@pytest.mark.django_db()
def test_options_llm_models_are_empty_without_an_llm_provider(team):
    LlmProviderModelFactory.create(team=None, type="openai", name="gpt-5.1")

    client = ApiTestClient(team.members.first(), team)
    options = client.get(reverse("api:v2:pipeline-options")).json()

    assert options["llm_provider_id"] == []
    assert options["llm_provider_model_id"] == []
    assert options["default_llm_provider"] == {"llm_provider_id": None, "llm_provider_model_id": None}


@pytest.mark.django_db()
def test_options_include_a_valid_starting_provider_pair(team_with_resources):
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    defaults = client.get(reverse("api:v2:pipeline-options")).json()["default_llm_provider"]

    assert defaults["llm_provider_id"] is not None
    assert defaults["llm_provider_model_id"] is not None


@pytest.mark.django_db()
def test_options_can_be_scoped_to_one_node_type(team_with_resources):
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    scoped = client.get(reverse("api:v2:pipeline-options"), {"node_type": "RenderTemplate"}).json()

    assert set(scoped) == {"template_variables"}


@pytest.mark.django_db()
def test_scoped_options_keep_the_provider_defaults_for_llm_nodes(team_with_resources):
    """A scoped response still has to be enough to build the node on its own."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    scoped = client.get(reverse("api:v2:pipeline-options"), {"node_type": "LLMResponseWithPrompt"}).json()

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
def test_scoped_options_404_like_the_node_list(team_with_resources, node_type, expected_detail):
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    response = client.get(reverse("api:v2:pipeline-options"), {"node_type": node_type})

    assert response.status_code == 404
    assert expected_detail in response.json()["detail"]
    assert sorted(response.json()) == ["detail", "valid_types"]


@pytest.mark.django_db()
def test_a_node_type_that_references_nothing_scopes_to_an_empty_object(team_with_resources):
    """`CodeNode` is addable and every param is free text, so it scopes to nothing -- which is a
    different answer from "no such type"."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)

    response = client.get(reverse("api:v2:pipeline-options"), {"node_type": "CodeNode"})

    assert response.status_code == 200
    assert response.json() == {}


def test_the_documented_example_carries_every_key_the_serializer_declares():
    """A reader takes the response sample for the whole payload, so a key the sample omits reads as
    a key the endpoint doesn't serve."""
    assert list(PIPELINE_OPTIONS_EXAMPLE) == list(PipelineOptionsSerializer().fields)


@pytest.mark.django_db()
def test_options_never_expose_provider_config(team_with_resources):
    """Providers are reference-only -- their `config` holds credentials."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    body = client.get(reverse("api:v2:pipeline-options")).content.decode()

    assert "openai_api_key" not in body
    assert "aws_secret_access_key" not in body


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
