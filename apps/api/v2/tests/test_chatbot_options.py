"""The contract `/chatbot/options/` keeps: that it serves what the settings form offers, scoped to
the same team, and that the payload stays documented as the form grows.

Which resources the form itself offers is covered next to the form, in
`apps/chatbots/tests/test_chatbot_forms.py`.
"""

import pytest
from django.urls import reverse
from rest_framework import serializers
from rest_framework.test import APIClient

from apps.api.v2.discovery.chatbot_settings import chatbot_setting_options
from apps.api.v2.discovery.serializers import ChatbotOptionsSerializer
from apps.api.v2.discovery.views import CHATBOT_OPTIONS_EXAMPLE
from apps.experiments.models import SyntheticVoice, VoiceResponseBehaviours
from apps.teams.models import Flag
from apps.utils.factories.experiment import ConsentFormFactory, SyntheticVoiceFactory
from apps.utils.factories.service_provider_factories import TraceProviderFactory, VoiceProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.tests.clients import ApiTestClient


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def team_with_every_resource(team):
    """One entry in every option list, so a shape assertion has something to look at in each."""
    provider = VoiceProviderFactory.create(team=team, name="Prod Polly")
    # One voice tied to the team's provider and one of the shared AWS voices, which carry no provider.
    SyntheticVoiceFactory.create(name="Joanna", service="AWS", voice_provider=provider)
    SyntheticVoiceFactory.create(name="Matthew", service="AWS")
    TraceProviderFactory.create(team=team, name="Prod Langfuse")
    ConsentFormFactory.create(team=team, name="Returns consent")
    return team


def _options(team, **params) -> dict:
    client = ApiTestClient(team.members.first(), team)
    response = client.get(reverse("api:v2:chatbot-options"), params)
    assert response.status_code == 200, response.content
    return response.json()


def _enable_voice_engine_flag_for(team):
    flag, _ = Flag.objects.get_or_create(name="flag_open_ai_voice_engine")
    flag.teams.add(team)
    flag.flush()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "auth_method",
    [
        pytest.param("api_key", id="api-key"),
        pytest.param("oauth", id="oauth"),
    ],
)
def test_serves_the_settings_that_take_a_fixed_set_of_values(auth_method, team_with_every_resource):
    """The endpoint answers "what may I write into this setting", so every setting drawn from a list
    has to be here -- and the ones that take any string or any boolean must not be, because an empty
    list there reads as "nothing is allowed"."""
    client = ApiTestClient(team_with_every_resource.members.first(), team_with_every_resource, auth_method=auth_method)

    options = client.get(reverse("api:v2:chatbot-options")).json()

    assert set(options) == {
        "voice_provider",
        "synthetic_voice",
        "voice_response_behaviour",
        "trace_provider",
        "consent_form",
    }
    assert all(entries for entries in options.values()), options


@pytest.mark.django_db()
def test_every_option_bearing_form_field_is_served(team_with_every_resource, rf):
    """The payload is derived from the form rather than listed by hand. A serializer that declares
    only some of the derived keys would drop the rest silently, so the two are held together here."""
    request = rf.get("/")
    request.team = team_with_every_resource
    request.user = team_with_every_resource.members.first()

    served = _options(team_with_every_resource)

    assert set(served) == set(chatbot_setting_options(request))


@pytest.mark.django_db()
def test_only_the_teams_own_resources_are_offered(team_with_every_resource):
    """Every list is a write target, so another team's consent form appearing here is an invitation
    to point a chatbot at a resource it may not read."""
    other_team = TeamWithUsersFactory.create()
    ConsentFormFactory.create(team=other_team, name="Other team consent")
    TraceProviderFactory.create(team=other_team, name="Other team Langfuse")
    VoiceProviderFactory.create(team=other_team, name="Other team Polly")

    options = _options(team_with_every_resource)

    labels = {entry["label"] for entries in options.values() for entry in entries}
    assert not [label for label in labels if "Other team" in label], labels


@pytest.mark.django_db()
def test_versioned_consent_forms_are_not_offered(team_with_every_resource):
    """A version is a historical copy of a consent form, not a form a chatbot may be pointed at."""
    working = team_with_every_resource.consentform_set.get(name="Returns consent")
    ConsentFormFactory.create(team=team_with_every_resource, name="Old consent", working_version=working)

    labels = [entry["label"] for entry in _options(team_with_every_resource)["consent_form"]]

    assert "Returns consent" in labels
    assert "Old consent" not in labels


@pytest.mark.django_db()
def test_a_flagged_off_voice_service_is_offered_to_neither_the_ui_nor_the_api(team):
    """The form hides the OpenAI voice engine behind a team flag. The API reads its lists off that
    same form, so a team without the flag must not be told it can write those voices."""
    provider = VoiceProviderFactory.create(team=team, type="openai", name="Prod OpenAI Voice")
    SyntheticVoiceFactory.create(
        name="Alloy", service=SyntheticVoice.OpenAIVoiceEngine, voice_provider=provider, language="English"
    )

    without_flag = _options(team)
    _enable_voice_engine_flag_for(team)
    with_flag = _options(team)

    assert not [entry for entry in without_flag["synthetic_voice"] if "Alloy" in entry["label"]]
    assert [entry for entry in with_flag["synthetic_voice"] if "Alloy" in entry["label"]]
    # The provider is hidden with its voices: it can speak nothing the team is allowed to choose.
    assert not [entry for entry in without_flag["voice_provider"] if "Prod OpenAI Voice" in entry["label"]]
    assert [entry for entry in with_flag["voice_provider"] if "Prod OpenAI Voice" in entry["label"]]


@pytest.mark.django_db()
def test_a_voice_carries_what_it_takes_to_pair_it_with_a_provider(team_with_every_resource):
    """A chatbot's voice and voice provider are written together and only agree when the voice's
    service matches the provider's type, so each voice has to say which provider can speak it."""
    provider_id = team_with_every_resource.voiceprovider_set.get().id
    owned_id = SyntheticVoice.objects.get(voice_provider=provider_id).id
    shared_id = SyntheticVoice.objects.filter(voice_provider=None, service="AWS").first().id

    options = _options(team_with_every_resource)

    by_id = {entry["value"]: entry for entry in options["synthetic_voice"]}
    assert {entry["value"] for entry in options["voice_provider"]} == {provider_id}
    assert options["voice_provider"][0]["type"] == "aws"
    assert (by_id[owned_id]["type"], by_id[owned_id]["provider_id"]) == ("aws", provider_id)
    assert (by_id[shared_id]["type"], by_id[shared_id]["provider_id"]) == ("aws", None)


@pytest.mark.django_db()
def test_a_choice_setting_serves_its_values_not_its_labels(team):
    """`voice_response_behaviour` is stored as the choice's value; a client writing back the label
    it displayed would write something the model rejects."""
    behaviours = _options(team)["voice_response_behaviour"]

    assert [entry["value"] for entry in behaviours] == list(VoiceResponseBehaviours.values)
    assert [entry["label"] for entry in behaviours] == list(VoiceResponseBehaviours.labels)


@pytest.mark.django_db()
def test_the_empty_choice_is_not_served_as_a_value(team):
    """The form's blank entry stands for "nothing chosen". It is not a value a client can write."""
    options = _options(team)

    assert not [entry for entries in options.values() for entry in entries if entry["value"] in ("", None)]


@pytest.mark.django_db()
def test_a_team_with_no_resources_gets_empty_lists_rather_than_missing_keys(team):
    """A client reads "you have configured no voice providers" off an empty list; a missing key
    leaves it unable to tell that from a key it has never heard of."""
    options = _options(team)

    assert options["voice_provider"] == []
    assert options["trace_provider"] == []
    assert options["voice_response_behaviour"], "the fixed choices do not depend on the team's resources"


@pytest.mark.django_db()
def test_requires_authentication():
    assert APIClient().get(reverse("api:v2:chatbot-options")).status_code == 401


@pytest.mark.django_db()
def test_a_read_only_key_may_read_the_options(team_with_every_resource):
    """Reading what a setting accepts changes nothing, so the read-only API key gate must not block
    it (ADR-0021)."""
    client = ApiTestClient(team_with_every_resource.members.first(), team_with_every_resource, read_only=True)

    assert client.get(reverse("api:v2:chatbot-options")).status_code == 200


def test_the_documented_example_carries_every_key_the_serializer_declares():
    """A reader takes the response sample for the whole payload, so a key the sample omits reads as
    a key the endpoint doesn't serve."""
    assert list(CHATBOT_OPTIONS_EXAMPLE) == list(ChatbotOptionsSerializer().fields)


def _documented_option_shapes() -> dict[str, serializers.Serializer]:
    """Every key holding a list of options, and the serializer documenting one entry of it."""
    return {
        key: field.child
        for key, field in ChatbotOptionsSerializer().fields.items()
        if isinstance(getattr(field, "child", None), serializers.Serializer)
    }


OPTION_LIST_KEYS = sorted(_documented_option_shapes())


@pytest.mark.django_db()
@pytest.mark.parametrize("key", OPTION_LIST_KEYS)
def test_every_option_list_documents_the_fields_it_serves(team_with_every_resource, key):
    """The lists share no single option shape -- `type` belongs to the provider-backed ones and
    `provider_id` only to `synthetic_voice` -- so one shape documented for all of them puts fields
    on lists that will never carry them."""
    documented = _documented_option_shapes()[key]

    entries = _options(team_with_every_resource)[key]

    assert entries, f"{key} came back empty, so it holds the docs to nothing"
    assert {name for entry in entries for name in entry} == set(documented.fields)


@pytest.mark.django_db()
@pytest.mark.parametrize("key", OPTION_LIST_KEYS)
def test_every_option_value_has_the_documented_type(team_with_every_resource, key):
    """`value` is written straight into a setting, so an id documented as an integer and served as a
    string is a break even though both are JSON scalars."""
    documented_value = _documented_option_shapes()[key].fields["value"]
    expected_type = {serializers.IntegerField: int, serializers.CharField: str}[type(documented_value)]

    entries = _options(team_with_every_resource)[key]

    assert entries, f"{key} came back empty, so it holds the docs to nothing"
    assert all(isinstance(entry["value"], expected_type) for entry in entries), entries


@pytest.mark.django_db()
def test_an_undeclared_key_is_served_rather_than_dropped(team, monkeypatch):
    """The point of deriving the payload from the form is that a new settings field needs no change
    here. A serializer that only passes its declared keys would swallow it instead."""
    monkeypatch.setattr(
        "apps.api.v2.discovery.views.chatbot_setting_options",
        lambda request: {"a_new_setting": [{"value": "yes", "label": "Yes"}]},
    )

    assert _options(team)["a_new_setting"] == [{"value": "yes", "label": "Yes"}]
