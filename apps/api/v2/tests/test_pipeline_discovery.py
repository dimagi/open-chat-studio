import pytest
from django.urls import reverse

from apps.api.v2.discovery import _clean_options
from apps.utils.factories.documents import CollectionFactory
from apps.utils.factories.experiment import SourceMaterialFactory, SyntheticVoiceFactory
from apps.utils.factories.service_provider_factories import (
    LlmProviderFactory,
    LlmProviderModelFactory,
    VoiceProviderFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.prompt import PROMPT_VAR_DESCRIPTIONS
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


def test_pipeline_nodes_reverse_is_versioned():
    assert reverse("api:v2:pipeline-nodes") == "/api/v2/pipeline/nodes/"


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
def test_deprecated_node_types_are_excluded(team):
    client = ApiTestClient(team.members.first(), team)
    types = {entry["type"] for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    assert {"AssistantNode", "BooleanNode", "LLMResponse"}.isdisjoint(types)


@pytest.mark.django_db()
def test_structural_node_types_are_listed_but_flagged_not_addable(team):
    """Start/End/Passthrough appear in every inspected graph, so the agent must be able to resolve
    them here -- but it must not create them."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    assert by_type["StartNode"]["can_add"] is False
    assert by_type["EndNode"]["can_add"] is False
    assert by_type["Passthrough"]["can_add"] is False
    assert by_type["LLMResponseWithPrompt"]["can_add"] is True


@pytest.mark.django_db()
def test_root_level_ui_keys_are_stripped(team):
    """Only root-level `ui:*` keys (`ui:deprecated`, `ui:can_add`) are stripped. Per-property `ui:*`
    keys -- e.g. `ui:optionsSource` -- are deliberately retained; see the companion test below."""
    client = ApiTestClient(team.members.first(), team)
    for entry in client.get(reverse("api:v2:pipeline-nodes")).json():
        assert not [key for key in entry["schema"] if key.startswith("ui:")], entry["type"]


@pytest.mark.django_db()
def test_property_level_ui_options_source_survives(team):
    """`ui:optionsSource` is the only machine-readable join from a node param to an `/options/` key;
    a future "strip all ui: keys" cleanup must not silently sever it."""
    client = ApiTestClient(team.members.first(), team)
    by_type = {entry["type"]: entry for entry in client.get(reverse("api:v2:pipeline-nodes")).json()}

    source_material_id = by_type["LLMResponseWithPrompt"]["schema"]["properties"]["source_material_id"]
    assert source_material_id["ui:optionsSource"] == "source_material"


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
    ],
)
def test_type_filter_404s(team, node_type):
    client = ApiTestClient(team.members.first(), team)
    assert client.get(reverse("api:v2:pipeline-nodes"), {"type": node_type}).status_code == 404


@pytest.mark.django_db()
def test_unauthenticated_request_is_rejected(team, client):
    assert client.get(reverse("api:v2:pipeline-nodes")).status_code == 401


@pytest.mark.django_db()
def test_read_only_api_key_may_read(team):
    """The inspect key is read-only; discovery is a GET, so it must work."""
    client = ApiTestClient(team.members.first(), team, read_only=True)
    assert client.get(reverse("api:v2:pipeline-nodes")).status_code == 200


@pytest.fixture()
def team_with_resources(db):
    team = TeamWithUsersFactory.create()
    LlmProviderFactory.create(team=team, type="openai", name="Prod OpenAI")
    LlmProviderModelFactory.create(team=team, type="openai")
    VoiceProviderFactory.create(team=team, name="Prod Polly")
    SourceMaterialFactory.create(team=team, topic="Returns policy")
    # CollectionFactory's SubFactories would otherwise auto-create an LlmProvider and an
    # EmbeddingProviderModel on this team, polluting the LlmProviderId assertions below.
    # A media collection (is_index=False) has neither in production anyway.
    CollectionFactory.create(
        team=team, name="Policy docs", is_index=False, llm_provider=None, embedding_provider_model=None
    )
    return team


def test_pipeline_options_reverse_is_versioned():
    assert reverse("api:v2:pipeline-options") == "/api/v2/pipeline/options/"


@pytest.mark.django_db()
@pytest.mark.parametrize("auth_method", ["api_key", "oauth"])
def test_options_lists_team_resources(auth_method, team_with_resources):
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources, auth_method=auth_method)
    response = client.get(reverse("api:v2:pipeline-options"))

    assert response.status_code == 200
    options = response.json()
    assert [option["label"] for option in options["LlmProviderId"]] == ["Prod OpenAI"]
    assert [option["label"] for option in options["source_material"]] == ["Returns policy"]
    assert [option["label"] for option in options["collection"]] == ["Policy docs"]


@pytest.mark.django_db()
def test_options_are_team_scoped(team_with_resources):
    """Another team's resources must not leak into this team's options."""
    other_team = TeamWithUsersFactory.create()
    LlmProviderFactory.create(team=other_team, name="Their OpenAI")
    VoiceProviderFactory.create(team=other_team, name="Their Polly")
    SourceMaterialFactory.create(team=other_team, topic="Their policy")
    # Same SubFactory pollution as in `team_with_resources` above -- pin to None or an unwanted
    # LlmProvider on `other_team` would land in the leak-check labels below.
    CollectionFactory.create(
        team=other_team, name="Their docs", is_index=False, llm_provider=None, embedding_provider_model=None
    )

    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    options = client.get(reverse("api:v2:pipeline-options")).json()

    labels = {
        label
        for key in ("LlmProviderId", "VoiceProviderId", "source_material", "collection")
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
    """`built_in_tools` is a dict of lists keyed by provider type -- a `_clean_options` that only
    special-cased the top-level-list case would silently skip it, leaving placeholder entries and
    `edit_url` buried at depth >= 2 untouched. `edit_url` is exercised at depth 3 via
    `built_in_tools_config` for the same reason.

    Real `built_in_tools_config` entries (`BuiltInTools.get_tool_configs_by_provider`) are
    `{name, type, label, helpText}` descriptors with no `value` key at all, so the placeholder strip
    (which only matches `option.get("value") == ""`) can never touch them -- they must survive
    `_clean_options` unchanged. A fixture that gave them a `value` key, as an earlier version of this
    test did, would lock in "silently drop a config descriptor" as intended behaviour, which is not
    the actual contract."""
    nested = {
        "built_in_tools": {
            "openai": [
                {"value": "web-search", "label": "Web Search", "edit_url": "/tools/web-search"},
                {"value": "", "label": "Select a tool"},
            ],
        },
        "built_in_tools_config": {
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

    cleaned = _clean_options(nested)

    assert cleaned["built_in_tools"]["openai"] == [{"value": "web-search", "label": "Web Search"}]
    assert cleaned["built_in_tools_config"]["anthropic"]["web-search"] == [
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
    voice_providers = client.get(reverse("api:v2:pipeline-options")).json()["VoiceProviderId"]

    assert [option["label"] for option in voice_providers] == ["Prod Polly"]
    assert voice_providers[0]["type"] == "aws"


@pytest.mark.django_db()
def test_options_synthetic_voices_are_filtered_by_team_voice_provider_type(team_with_resources):
    """`SyntheticVoice.service` ("AWS") and `VoiceProviderType` ("aws") differ only in case -- a naive
    `service__in` match against the team's provider types would return nothing, the same trap the
    chatbot builder avoids with `service__iexact`. `team_with_resources` has an AWS voice provider,
    so AWS voices must be listed; Azure/OpenAI voices -- reachable to every team via `get_for_team`
    since none of the three are team-scoped services -- must not be, absent a matching provider."""
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
    """A team with no VoiceProvider can reach no synthetic voice, however many exist generally --
    this is the 564-unreachable-voices bug the filtering in the view fixes."""
    SyntheticVoiceFactory.create(service="AWS")
    SyntheticVoiceFactory.create(service="Azure")

    client = ApiTestClient(team.members.first(), team)
    options = client.get(reverse("api:v2:pipeline-options")).json()

    assert options["synthetic_voice_id"] == []


@pytest.mark.django_db()
def test_options_include_default_values(team_with_resources):
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    defaults = client.get(reverse("api:v2:pipeline-options")).json()["default_values"]

    assert defaults["llm_provider_id"] is not None
    assert defaults["llm_provider_model_id"] is not None


@pytest.mark.django_db()
def test_options_never_expose_provider_config(team_with_resources):
    """Providers are reference-only -- their `config` holds credentials."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    body = client.get(reverse("api:v2:pipeline-options")).content.decode()

    assert "openai_api_key" not in body
    assert "aws_secret_access_key" not in body


@pytest.mark.django_db()
def test_options_unauthenticated_request_is_rejected(team_with_resources, client):
    assert client.get(reverse("api:v2:pipeline-options")).status_code == 401


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "options_key",
    [
        pytest.param("text_editor_autocomplete_vars_llm_node", id="llm-node-vars"),
        pytest.param("text_editor_autocomplete_vars_router_node", id="router-node-vars"),
        pytest.param("jinja_node", id="jinja-node-vars"),
    ],
)
def test_prompt_var_options_carry_a_description_not_a_value(team_with_resources, options_key):
    """The builder emits {"label": v, "value": v} -- the two are always identical, so the value
    tells an agent nothing. It gets a description of what the variable holds instead."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    entries = client.get(reverse("api:v2:pipeline-options")).json()[options_key]

    assert entries
    for entry in entries:
        assert sorted(entry) == ["description", "label"], entry
        assert entry["description"].strip()
        assert entry["description"] != entry["label"]


@pytest.mark.django_db()
def test_prompt_var_descriptions_are_the_real_ones(team_with_resources):
    """Pins one description end-to-end so a refactor can't quietly serve placeholder text."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    by_label = {
        entry["label"]: entry["description"]
        for entry in client.get(reverse("api:v2:pipeline-options")).json()["jinja_node"]
    }

    assert by_label["input"] == PROMPT_VAR_DESCRIPTIONS["input"]
    assert "session" in by_label["session_state"]
    assert "run" in by_label["temp_state"]


@pytest.mark.django_db()
def test_resource_option_lists_keep_their_value(team_with_resources):
    """Only the prompt-variable keys are reshaped -- resource lists still carry the id to write."""
    client = ApiTestClient(team_with_resources.members.first(), team_with_resources)
    options = client.get(reverse("api:v2:pipeline-options")).json()

    assert options["source_material"][0]["value"]
    assert "description" not in options["source_material"][0]
