import re
from datetime import timedelta
from unittest import mock

import httpx
import pytest
from django.urls import reverse
from django.utils import timezone

from apps.channels.models import ChannelPlatform
from apps.chat.exceptions import ServiceWindowExpiredException
from apps.service_providers.messaging_service import MetaCloudAPIService
from apps.service_providers.models import (
    AuthProvider,
    EmbeddingProviderModel,
    LlmProvider,
    LlmProviderModel,
    LlmProviderTypes,
    MessagingProvider,
    MessagingProviderType,
    TraceProvider,
    VoiceProvider,
    VoiceProviderType,
)
from apps.service_providers.utils import ServiceProvider
from apps.service_providers.views import _format_context
from apps.utils.factories.evaluations import EvaluatorFactory
from apps.utils.factories.pipelines import NodeFactory
from apps.utils.factories.service_provider_factories import (
    AuthProviderFactory,
    LlmProviderFactory,
    LlmProviderModelFactory,
    MessagingProviderFactory,
    TraceProviderFactory,
    VoiceProviderFactory,
)


def factory_for_model(model):
    factory = {
        LlmProvider: LlmProviderFactory,
        VoiceProvider: VoiceProviderFactory,
        MessagingProvider: MessagingProviderFactory,
        AuthProvider: AuthProviderFactory,
        TraceProvider: TraceProviderFactory,
    }.get(model)

    return factory


@pytest.fixture()
def authed_client(team_with_users, client):
    user = team_with_users.members.first()
    client.force_login(user)
    return client


@pytest.mark.parametrize("provider", list(ServiceProvider))
@pytest.mark.django_db()
def test_table_view(provider, team_with_users, authed_client):
    factory = factory_for_model(provider.model)
    factory.create_batch(5, team=team_with_users)
    assert provider.model.objects.filter(team=team_with_users).count() == 5

    response = authed_client.get(
        reverse("service_providers:table", kwargs={"team_slug": team_with_users.slug, "provider_type": provider.slug})
    )
    assert response.status_code == 200
    assert len(response.context["table"].rows) == 5


@pytest.mark.parametrize("provider", list(ServiceProvider))
@pytest.mark.django_db()
def test_create_view(provider, team_with_users, authed_client):
    """Test that the create view renders without error."""
    subtype = next(iter(provider.subtype))
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={
                "team_slug": team_with_users.slug,
                "provider_type": provider.slug,
                "subtype": str(subtype),
            },
        )
    )
    assert response.status_code == 200


@pytest.mark.parametrize("provider", list(ServiceProvider))
@pytest.mark.django_db()
def test_update_view(provider, team_with_users, authed_client):
    """Test that the update view renders without error."""
    factory = factory_for_model(provider.model)
    provider_instance = factory(team=team_with_users)
    response = authed_client.get(
        reverse(
            "service_providers:edit",
            kwargs={"team_slug": team_with_users.slug, "provider_type": provider.slug, "pk": provider_instance.pk},
        )
    )
    assert response.status_code == 200


@pytest.mark.django_db()
def test_llm_provider_create_view_shows_create_and_verify_button(team_with_users, authed_client):
    """The create-page button says up front that saving will also verify credentials - static
    text, no Alpine needed, since a fresh provider has no prior config to react to."""
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "subtype": "openai"},
        )
    )
    content = response.content.decode()
    assert "Create and Verify" in content
    assert 'x-text="configChanged' not in content


@pytest.mark.django_db()
def test_voice_provider_create_view_shows_plain_create_button(team_with_users, authed_client):
    """Regression: every other provider type's create button renders as it always has."""
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "voice", "subtype": "aws"},
        )
    )
    content = response.content.decode()
    assert "and Verify" not in content


@pytest.mark.django_db()
def test_llm_provider_edit_view_shows_reactive_update_button(team_with_users, authed_client):
    """The edit-page button must be the Alpine-reactive one (default text "Update", swaps
    to "Update and Verify" once a credential field changes), not the static create-page one.

    Verified credentials are the case where the default label is a plain "Update" - an
    unverified provider already says "Update and Verify" before anything is edited.
    """
    provider = LlmProviderFactory(team=team_with_users, extra_data={"verified_credentials": True})
    response = authed_client.get(
        reverse(
            "service_providers:edit",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": provider.pk},
        )
    )
    content = response.content.decode()
    assert "configChanged ? 'Update and Verify' : 'Update'" in content
    assert ">Update</span>" in content


@pytest.mark.django_db()
def test_editing_credentials_only_changes_the_button_label(team_with_users, authed_client):
    """The button label is the whole notification. A banner that also announced the edit said
    the same thing twice, so the field listener now feeds only the label."""
    provider = LlmProviderFactory(team=team_with_users, extra_data={"verified_credentials": True})
    response = authed_client.get(
        reverse(
            "service_providers:edit",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": provider.pk},
        )
    )
    content = response.content.decode()
    assert 'x-on:input="configChanged = true"' in content
    assert 'x-show="configChanged"' not in content


@pytest.mark.django_db()
def test_non_llm_provider_edit_view_has_no_verify_affordances(team_with_users, authed_client):
    """Only LLM providers have credentials to verify, so only they get the reactive button
    and the field listener behind it."""
    provider = VoiceProviderFactory(team=team_with_users)
    response = authed_client.get(
        reverse(
            "service_providers:edit",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "voice", "pk": provider.pk},
        )
    )
    content = response.content.decode()
    assert "and Verify" not in content
    assert "configChanged" not in content


@pytest.mark.parametrize("provider", list(ServiceProvider))
@pytest.mark.django_db()
def test_delete_view(provider, team_with_users, authed_client):
    factory = factory_for_model(provider.model)
    provider_instance = factory(team=team_with_users)
    response = authed_client.delete(
        reverse(
            "service_providers:delete",
            kwargs={"team_slug": team_with_users.slug, "provider_type": provider.slug, "pk": provider_instance.pk},
        )
    )
    assert response.status_code == 200
    assert provider.model.objects.filter(team=team_with_users).count() == 0


@pytest.mark.django_db()
def test_sync_voices_endpoint(team_with_users, authed_client):
    """POST to sync-voices endpoint should call sync_voices on the provider"""

    provider = VoiceProvider.objects.create(
        team=team_with_users,
        name="ElevenLabs Test",
        type=VoiceProviderType.elevenlabs,
        config={"elevenlabs_api_key": "test_key", "elevenlabs_model": "eleven_multilingual_v2"},
    )
    url = reverse(
        "service_providers:sync_voices",
        kwargs={
            "team_slug": team_with_users.slug,
            "provider_type": "voice",
            "pk": provider.pk,
        },
    )
    with mock.patch.object(VoiceProvider, "sync_voices") as mock_sync:
        response = authed_client.post(url)

    assert response.status_code == 302
    mock_sync.assert_called_once()


class _FakeTransientError(Exception):
    """Stands in for a provider SDK error carrying an HTTP status code, without needing
    to construct a real openai/anthropic exception in the test."""

    status_code = 429


def _testable_llm_provider(team):
    """A provider with one model of its own type, so the test reaches the provider call.

    Creates the model rather than relying on the migration-seeded rows - see the
    "migration-seeded global rows" invariant in AGENTS.md.
    """
    provider = LlmProviderFactory(team=team)
    LlmProviderModelFactory(team=team, type=provider.type, name="gpt-4o-mini")
    return provider


def _openai_timeout_error():
    import openai  # noqa: PLC0415 - heavy lib, slow startup

    return openai.APITimeoutError(httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))


@pytest.mark.django_db()
class TestSavingVerifiesCredentials:
    """Saving credentials verifies them. A failure sends the user back to the provider's own
    edit page with the provider's own error, so the credentials it is about are on screen."""

    def _edit_url(self, team, provider):
        return reverse(
            "service_providers:edit",
            kwargs={"team_slug": team.slug, "provider_type": "llm", "pk": provider.pk},
        )

    def _messages(self, response):
        return [str(m) for m in response.context["messages"]]

    def test_changed_credentials_are_verified(self, team_with_users, authed_client):
        provider = LlmProviderFactory(team=team_with_users)

        with mock.patch.object(LlmProvider, "test_connection") as tested:
            authed_client.post(
                self._edit_url(team_with_users, provider),
                data={"name": provider.name, "openai_api_key": "a-brand-new-key"},
            )

        tested.assert_called_once()

    def test_an_unrelated_edit_does_not_spend_an_external_call(self, team_with_users, authed_client):
        """Renaming is not a credential change, so an already-verified provider has nothing
        new to verify."""
        provider = LlmProviderFactory(team=team_with_users, extra_data={"verified_credentials": True})
        api_key = provider.config.get("openai_api_key")

        with mock.patch.object(LlmProvider, "test_connection") as tested:
            authed_client.post(
                self._edit_url(team_with_users, provider),
                data={"name": "New Name", "openai_api_key": api_key},
            )

        tested.assert_not_called()
        provider.refresh_from_db()
        assert provider.name == "New Name"

    def test_a_pass_returns_to_the_team_list(self, team_with_users, authed_client):
        """Nothing to act on means no reason to detour - the save behaves like every other."""
        provider = LlmProviderFactory(team=team_with_users)
        team_list_url = reverse("single_team:manage_team", kwargs={"team_slug": team_with_users.slug})

        with mock.patch.object(LlmProvider, "test_connection"):
            response = authed_client.post(
                self._edit_url(team_with_users, provider),
                data={"name": provider.name, "openai_api_key": "a-brand-new-key"},
                follow=True,
            )

        assert response.redirect_chain == [(team_list_url, 302)]
        assert self._messages(response) == []

    def test_a_failure_lands_back_on_the_edit_page_with_the_raw_error(self, team_with_users, authed_client):
        """The warning is about the credentials in the form, so the form is where it belongs.
        The flash message stays short and the provider's own words render on the page, where
        there is room for them."""
        provider = LlmProviderFactory(team=team_with_users)
        url = self._edit_url(team_with_users, provider)
        error = Exception("Error code: 401 - Incorrect API key provided: sk-p***lt")

        with mock.patch.object(LlmProvider, "test_connection", side_effect=error):
            response = authed_client.post(url, data={"name": provider.name, "openai_api_key": "a-bad-key"}, follow=True)

        assert response.redirect_chain == [(url, 302)]
        warnings = self._messages(response)
        assert len(warnings) == 1
        assert "could not be verified" in warnings[0]
        assert "sk-p***lt" not in warnings[0]

        content = response.content.decode()
        assert "The provider rejected these credentials" in content
        assert "Incorrect API key provided: sk-p***lt" in content

    def test_the_raw_error_survives_a_reload(self, team_with_users, authed_client):
        """It describes the credentials that are still saved, so leaving the page and coming
        back has to still say why they were rejected."""
        provider = LlmProviderFactory(team=team_with_users)
        url = self._edit_url(team_with_users, provider)

        with mock.patch.object(LlmProvider, "test_connection", side_effect=Exception("kaboom")):
            first = authed_client.post(url, data={"name": provider.name, "openai_api_key": "bad"}, follow=True)
        assert "kaboom" in first.content.decode()

        second = authed_client.get(url)

        assert "kaboom" in second.content.decode()

    def test_a_pass_clears_the_error_from_the_page(self, team_with_users, authed_client):
        """Once the credentials pass there is nothing left to explain, so the banner goes."""
        provider = LlmProviderFactory(
            team=team_with_users,
            extra_data={"verified_credentials": False, "verification_error": "Exception: kaboom"},
        )
        url = self._edit_url(team_with_users, provider)

        with mock.patch.object(LlmProvider, "test_connection"):
            authed_client.post(url, data={"name": provider.name, "openai_api_key": "a-good-key"})

        assert "kaboom" not in authed_client.get(url).content.decode()

    def test_no_configured_model_shows_no_error_banner(self, team_with_users, authed_client):
        """Nothing reached the provider, so there is no provider response to show - the
        toast pointing at the Models tab is the whole message."""
        provider = LlmProviderFactory(team=team_with_users)
        LlmProviderModel.objects.filter(type=provider.type).delete()
        url = self._edit_url(team_with_users, provider)

        response = authed_client.post(url, data={"name": provider.name, "openai_api_key": "a-new-key"}, follow=True)

        assert "The provider rejected these credentials" not in response.content.decode()

    def test_no_configured_model_also_lands_on_the_edit_page(self, team_with_users, authed_client):
        """Nothing is wrong with the credentials - there is just nothing to verify against,
        and the Models tab it points at is on this page."""
        provider = LlmProviderFactory(team=team_with_users)
        LlmProviderModel.objects.filter(type=provider.type).delete()
        url = self._edit_url(team_with_users, provider)

        response = authed_client.post(url, data={"name": provider.name, "openai_api_key": "a-new-key"}, follow=True)

        assert response.redirect_chain == [(url, 302)]
        assert "Models tab" in self._messages(response)[0]

    def test_an_untestable_provider_type_is_not_detoured(self, team_with_users, authed_client):
        """Voyage AI can never pass this check, so sending the user to the edit page every
        time they save would be a dead end, not a next step."""
        provider = LlmProviderFactory(team=team_with_users, type=str(LlmProviderTypes.voyage))
        team_list_url = reverse("single_team:manage_team", kwargs={"team_slug": team_with_users.slug})

        response = authed_client.post(
            self._edit_url(team_with_users, provider),
            data={"name": provider.name, "voyage_api_key": "a-new-key"},
            follow=True,
        )

        assert response.redirect_chain == [(team_list_url, 302)]

    def test_an_unverified_provider_is_verified_even_when_nothing_changed(self, team_with_users, authed_client):
        """The whole point of the stored flag: a check that failed - or never ran - stays
        retryable by saving, without the user having to edit a credential to force it."""
        provider = LlmProviderFactory(team=team_with_users, extra_data={"verified_credentials": False})
        api_key = provider.config.get("openai_api_key")

        with mock.patch.object(LlmProvider, "test_connection") as tested:
            authed_client.post(
                self._edit_url(team_with_users, provider),
                data={"name": provider.name, "openai_api_key": api_key},
            )

        tested.assert_called_once()

    def test_a_pass_records_the_credentials_as_verified(self, team_with_users, authed_client):
        """End to end: saving through the view is what leaves the flag behind."""
        provider = LlmProviderFactory(team=team_with_users)

        with mock.patch.object(LlmProvider, "test_connection"):
            authed_client.post(
                self._edit_url(team_with_users, provider),
                data={"name": provider.name, "openai_api_key": "a-brand-new-key"},
            )

        provider.refresh_from_db()
        assert provider.credentials_verified is True

    @pytest.mark.parametrize(
        ("extra_data", "expected_label"),
        [
            pytest.param({}, "Update and Verify", id="never-verified"),
            pytest.param({"verified_credentials": False}, "Update and Verify", id="verification-failed"),
            pytest.param({"verified_credentials": True}, "Update", id="verified"),
        ],
    )
    def test_the_button_says_whether_saving_will_verify(
        self, team_with_users, authed_client, extra_data, expected_label
    ):
        provider = LlmProviderFactory(team=team_with_users, extra_data=extra_data)

        response = authed_client.get(self._edit_url(team_with_users, provider))

        assert response.context["button_text"] == expected_label

    @pytest.mark.parametrize(
        ("extra_data", "expected_line"),
        [
            pytest.param({}, "have not been checked yet", id="never-verified"),
            pytest.param({"verified_credentials": False}, "have not been checked yet", id="verification-failed"),
            pytest.param({"verified_credentials": True}, "Credentials verified", id="verified"),
        ],
    )
    def test_the_page_says_where_the_credentials_stand(self, team_with_users, authed_client, extra_data, expected_line):
        """The stored flag has to be readable without inferring it from the button label."""
        provider = LlmProviderFactory(team=team_with_users, extra_data=extra_data)

        response = authed_client.get(self._edit_url(team_with_users, provider))

        assert expected_line in response.content.decode()

    def test_the_rejection_replaces_the_verification_line(self, team_with_users, authed_client):
        """The provider's own words say where the credentials stand better than the line does."""
        provider = LlmProviderFactory(
            team=team_with_users,
            extra_data={"verified_credentials": False, "verification_error": "Exception: kaboom"},
        )

        response = authed_client.get(self._edit_url(team_with_users, provider))

        content = response.content.decode()
        assert "The provider rejected these credentials" in content
        assert "have not been checked yet" not in content

    def test_an_untestable_provider_type_says_nothing_about_verification(self, team_with_users, authed_client):
        """Voyage AI can never be checked, so there is no state to report."""
        provider = LlmProviderFactory(team=team_with_users, type=str(LlmProviderTypes.voyage))

        response = authed_client.get(self._edit_url(team_with_users, provider))

        assert "credential-verification-state" not in response.content.decode()

    def test_an_untestable_provider_type_never_offers_to_verify(self, team_with_users, authed_client):
        """Voyage AI has no check to offer, so the label must not promise one and the
        credential fields have nothing to react to."""
        provider = LlmProviderFactory(team=team_with_users, type=str(LlmProviderTypes.voyage))

        response = authed_client.get(self._edit_url(team_with_users, provider))

        assert response.context["button_text"] == "Update"
        assert "configChanged" not in response.content.decode()

    def test_a_save_never_fails_because_the_provider_did(self, team_with_users, authed_client):
        """The check runs after the save transaction commits, so a provider outage cannot
        cost the user the credentials they just entered."""
        provider = LlmProviderFactory(team=team_with_users)

        with mock.patch.object(LlmProvider, "test_connection", side_effect=RuntimeError("boom")):
            authed_client.post(
                self._edit_url(team_with_users, provider),
                data={"name": "Saved Anyway", "openai_api_key": "a-new-key"},
            )

        provider.refresh_from_db()
        assert provider.name == "Saved Anyway"
        assert provider.config.get("openai_api_key") == "a-new-key"


@pytest.mark.django_db()
def test_updating_voice_provider_still_redirects_to_team_list(team_with_users, authed_client):
    """Regression: the edit-page redirect only applies when an LLM connection-test warning
    actually fired. Every other provider type must keep today's behavior unchanged."""
    provider = VoiceProviderFactory(team=team_with_users, name="Old Name")
    url = reverse(
        "service_providers:edit",
        kwargs={"team_slug": team_with_users.slug, "provider_type": "voice", "pk": provider.pk},
    )
    team_list_url = reverse("single_team:manage_team", kwargs={"team_slug": team_with_users.slug})

    response = authed_client.post(
        url,
        data={
            "name": "New Name",
            "aws_access_key_id": "new-id",
            "aws_secret_access_key": "new-secret",
            "aws_region": "us-east-1",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert response.redirect_chain == [(team_list_url, 302)]


@pytest.mark.django_db()
def test_delete_llm_provider_referenced_by_pipeline_nullifies_node_fk(team_with_users, authed_client):
    """Deleting an LLM provider referenced by a pipeline node succeeds (SET_NULL): the node's
    llm_provider FK is nulled, while params (authoritative) is left untouched.

    In practice this only happens for an archived pipeline: the delete guards block removing a
    provider that a live (working) node still references, so the FK is only nulled once the
    pipeline holding the node has been archived and the provider is then deleted.
    """
    provider = LlmProviderFactory(team=team_with_users)
    node = NodeFactory.create(
        type="LLMResponseWithPrompt",
        params={"llm_provider_id": provider.id},
        llm_provider=provider,
    )

    response = authed_client.delete(
        reverse(
            "service_providers:delete",
            kwargs={"team_slug": team_with_users.slug, "provider_type": ServiceProvider.llm.slug, "pk": provider.pk},
        )
    )

    assert response.status_code == 200
    assert not LlmProvider.objects.filter(pk=provider.pk).exists()
    node.refresh_from_db()
    assert node.llm_provider_id is None
    assert node.params["llm_provider_id"] == provider.id  # params unchanged (authoritative)


@pytest.mark.django_db()
def test_delete_llm_provider_blocked_by_an_evaluator(team_with_users, authed_client):
    """Evaluators reference the provider by FK, so deleting underneath one is blocked.

    Previously they were collected by get_related_objects but silently dropped, leaving the
    evaluator with a nulled FK and nothing to run against — with no warning.
    """
    provider = LlmProviderFactory(team=team_with_users)
    evaluator = EvaluatorFactory.create(team=team_with_users, llm_provider=provider)

    response = authed_client.delete(
        reverse(
            "service_providers:delete",
            kwargs={"team_slug": team_with_users.slug, "provider_type": ServiceProvider.llm.slug, "pk": provider.pk},
        )
    )

    assert response.status_code == 200
    assert evaluator.name in response.content.decode()
    assert LlmProvider.objects.filter(pk=provider.pk).exists()
    evaluator.refresh_from_db()
    assert evaluator.llm_provider_id == provider.id


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        pytest.param(0, "—", id="zero-disables-compression-rather-than-being-a-small-limit"),
        pytest.param(512, "512", id="under-1k-is-left-alone"),
        pytest.param(8192, "8K", id="8192"),
        pytest.param(128000, "128K", id="128k"),
        pytest.param(409600, "410K", id="rounds-to-whole-k"),
        pytest.param(1000000, "1M", id="1m-drops-the-decimal"),
        pytest.param(1500000, "1.5M", id="keeps-a-meaningful-decimal"),
    ],
)
def test_format_context(limit, expected):
    """Token limits are read as magnitudes, not counted."""
    assert _format_context(limit) == expected


@pytest.mark.django_db()
def test_models_tab_modals_are_not_inside_a_tab_panel(team_with_users, authed_client):
    """Regression: an unselected daisyUI tab panel is display:none, and a <dialog> under a
    hidden ancestor opens into the top layer without generating any boxes. Both dialogs are
    opened from the Models tab, so if they render inside the Configuration panel,
    showModal() silently does nothing - which is what happened.

    Asserted against the parsed tree rather than the HTML string: the string contains both
    the dialog and the panel either way, so only the nesting distinguishes the bug.
    """
    from lxml import html as lxml_html  # noqa: PLC0415 - test-only parser

    provider = LlmProviderFactory(team=team_with_users, type=str(LlmProviderTypes.openai))
    response = authed_client.get(
        reverse(
            "service_providers:edit",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": provider.pk},
        )
    )
    tree = lxml_html.fromstring(response.content)

    dialogs = tree.xpath("//dialog[@id='new_custom_model'] | //dialog[@id='pricing_override_modal']")
    assert len(dialogs) == 2, "both Models-tab dialogs should render on the edit page"
    for dialog in dialogs:
        assert not dialog.xpath("ancestor::*[contains(@class, 'tab-content')]"), (
            f"<dialog id={dialog.get('id')}> is inside a tab panel and cannot be shown"
        )


@pytest.mark.django_db()
def test_model_rows_have_unique_dom_ids(team_with_users, authed_client):
    """LlmProviderModel and EmbeddingProviderModel are separate tables with separate
    sequences, so their ids collide. The rows share one table, and each carries an
    hx-target pointing at its own id - a duplicate resolves to the wrong row and the swap
    lands on somebody else.
    """
    from lxml import html as lxml_html  # noqa: PLC0415 - test-only parser

    provider = LlmProviderFactory(team=team_with_users, type=str(LlmProviderTypes.openai))
    # Cleared rather than worked around: the suite runs with --reuse-db, so a test that
    # leans on the migration-seeded global rows is order-dependent.
    LlmProviderModel.objects.filter(type=provider.type).delete()
    EmbeddingProviderModel.objects.filter(type=provider.type).delete()
    llm_model = LlmProviderModelFactory(team=team_with_users, type=provider.type, name="a-chat-model")
    embedding = EmbeddingProviderModel.objects.create(team=None, type=provider.type, name="an-embedding-model")
    # The collision this guards against is two rows sharing a PK value across the tables.
    EmbeddingProviderModel.objects.filter(pk=embedding.pk).update(id=llm_model.pk)
    assert EmbeddingProviderModel.objects.filter(pk=llm_model.pk).exists(), "collision not set up"

    response = authed_client.get(
        reverse(
            "service_providers:edit",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": provider.pk},
        )
    )
    tree = lxml_html.fromstring(response.content)

    row_ids = tree.xpath("//tbody[@id='custom_model_list']/tr/@id")
    assert len(row_ids) == 2
    assert len(set(row_ids)) == len(row_ids), f"duplicate row ids: {row_ids}"
    assert set(row_ids) == {f"model_{llm_model.pk}", f"embedding_{llm_model.pk}"}


@pytest.mark.django_db()
def test_embedding_models_have_no_delete_button(team_with_users, authed_client):
    """A team-owned embedding model is "custom" too, but the delete endpoint only knows
    LlmProviderModel - offering delete here would target the wrong table's row.
    """
    provider = LlmProviderFactory(team=team_with_users, type=str(LlmProviderTypes.openai))
    LlmProviderModel.objects.filter(type=provider.type).delete()
    EmbeddingProviderModel.objects.filter(type=provider.type).delete()
    embedding = EmbeddingProviderModel.objects.create(
        team=team_with_users, type=provider.type, name="a-team-embedding-model"
    )

    response = authed_client.get(
        reverse(
            "service_providers:edit",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": provider.pk},
        )
    )

    assert b"a-team-embedding-model" in response.content
    assert f"delete-llm-model-{embedding.pk}".encode() not in response.content


@pytest.mark.django_db()
def test_models_tab_shows_empty_state_for_provider_with_no_models(team_with_users, authed_client):
    """LiteLLM ships no default models (every backend is install-specific, same as OpenRouter).

    The Models tab must say so rather than rendering an empty table, which is
    indistinguishable from a broken page.
    """
    provider = LlmProviderFactory(team=team_with_users, type=str(LlmProviderTypes.litellm))
    LlmProviderModel.objects.filter(type=provider.type).delete()

    response = authed_client.get(
        reverse(
            "service_providers:edit",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": provider.pk},
        )
    )

    assert response.status_code == 200
    assert b"No models are configured for this provider type" in response.content


@pytest.mark.django_db()
def test_models_tab_lists_global_and_team_models_together(team_with_users, authed_client):
    """One list, not three: a global chat model, a team-owned custom one and an embedding
    model all belong to the same question - what can this provider run?

    Creates its own rows rather than relying on the migration-seeded ones - see the
    "migration-seeded global rows" invariant in AGENTS.md.
    """
    provider = LlmProviderFactory(team=team_with_users, type=str(LlmProviderTypes.openai))
    LlmProviderModel.objects.filter(type=provider.type).delete()
    LlmProviderModelFactory(team=None, type=provider.type, name="test-default-model")
    LlmProviderModelFactory(team=team_with_users, type=provider.type, name="test-custom-model")

    response = authed_client.get(
        reverse(
            "service_providers:edit",
            kwargs={"team_slug": team_with_users.slug, "provider_type": "llm", "pk": provider.pk},
        )
    )
    content = response.content.decode()

    assert "test-default-model" in content
    assert "test-custom-model" in content
    assert "No models are configured for this provider type" not in content


@pytest.mark.django_db()
def test_create_view_404_for_filtered_subtype(team_with_users, authed_client, settings):
    """openai_voice_engine is gated by the flag_open_ai_voice_engine flag."""
    settings.SLACK_ENABLED = True  # ensure unrelated filter is off
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={
                "team_slug": team_with_users.slug,
                "provider_type": "voice",
                "subtype": VoiceProviderType.openai_voice_engine.value,
            },
        )
    )
    assert response.status_code == 404


@pytest.fixture()
def meta_provider(team_with_users):
    return MessagingProviderFactory(
        team=team_with_users,
        type=MessagingProviderType.meta_cloud_api,
        config={
            "business_id": "1285815180126064",
            "access_token": "token",
            "app_secret": "secret",
            "verify_token": "verify",
        },
        extra_data={
            "verify_token_hash": "abc123",
            "whatsapp_numbers": {
                "state": "ok",
                "synced_at": "2026-08-28T08:00:00+00:00",
                "numbers": [
                    {
                        "phone_number_id": "1020671484465717",
                        "number": "+27647084804",
                        "display": "+27 64 708 4804",
                        "verified_name": "TenantHive",
                    }
                ],
            },
        },
    )


def _whatsapp_url(name, provider):
    return reverse("service_providers:" + name, kwargs={"team_slug": provider.team.slug, "pk": provider.pk})


def _cache_template(provider, **status):
    provider.extra_data["whatsapp_template"] = {
        "ok": False,
        "checked_at": "2026-08-28T08:00:00+00:00",
        "problems": [],
        "error": None,
        "template": None,
        **status,
    }
    provider.save()
    return provider


@pytest.mark.django_db()
class TestWhatsappStatusView:
    """The panel renders from the cache. Only a refresh talks to Meta."""

    def test_never_calls_meta(self, meta_provider, authed_client):
        with mock.patch.object(MetaCloudAPIService, "check_message_template") as check:
            response = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider))

        assert response.status_code == 200
        check.assert_not_called()

    def test_renders_the_cached_check(self, meta_provider, authed_client):
        _cache_template(meta_provider, ok=True, template={"status": "APPROVED", "language": "en"})

        response = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider))

        assert response.context["template_ok"] is True
        assert response.context["template_checked"] is True
        assert "new_bot_message" in response.content.decode()

    def test_renders_a_cached_error(self, meta_provider, authed_client):
        _cache_template(meta_provider, error="(#190) Error validating access token")

        response = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider))

        assert "Error validating access token" in response.content.decode()

    def test_says_so_when_the_template_has_never_been_checked(self, meta_provider, authed_client):
        response = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider))

        assert response.context["template_checked"] is False
        assert response.context["template_ok"] is False
        assert "has not been checked with Meta yet" in response.content.decode()

    def test_the_refresh_button_targets_the_whole_panel(self, meta_provider, authed_client):
        """One refresh redraws the template block and the numbers together, so they never disagree."""
        content = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider)).content.decode()

        assert 'id="wa-status"' in content
        assert _whatsapp_url("whatsapp_refresh", meta_provider) in content
        assert 'hx-target="#wa-status"' in content

    def test_polls_itself_while_a_refresh_is_running(self, meta_provider, authed_client):
        meta_provider.mark_whatsapp_refresh_queued()

        content = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider)).content.decode()

        assert _whatsapp_url("whatsapp_status", meta_provider) in content
        assert 'hx-trigger="every 2s"' in content
        assert "Checking with Meta" in content

    def test_keeps_polling_once_the_numbers_land_but_the_template_check_has_not(self, meta_provider, authed_client):
        """The two legs commit separately, and the swap replaces the polling element itself.

        A poll answered between them must still carry the trigger, or polling dies for good
        and the panel is stuck on "Never checked" until someone hits Refresh.
        """
        meta_provider.mark_whatsapp_refresh_queued()
        with mock.patch.object(MetaCloudAPIService, "get_phone_numbers", return_value=[]):
            meta_provider.sync_whatsapp_numbers()

        content = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider)).content.decode()

        assert 'hx-trigger="every 2s"' in content
        assert "Checking with Meta" in content

    def test_stops_polling_once_the_whole_refresh_is_done(self, meta_provider, authed_client):
        meta_provider.mark_whatsapp_refresh_queued()
        meta_provider.mark_whatsapp_refresh_done()

        content = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider)).content.decode()

        assert 'hx-trigger="every 2s"' not in content

    def test_404_for_a_provider_of_another_type(self, team_with_users, authed_client):
        provider = MessagingProviderFactory(team=team_with_users, type=MessagingProviderType.twilio)

        response = authed_client.get(_whatsapp_url("whatsapp_status", provider))

        assert response.status_code == 404

    def test_404_for_another_teams_provider(self, authed_client, team_with_users):
        other_provider = MessagingProviderFactory(type=MessagingProviderType.meta_cloud_api, config={})
        url = reverse(
            "service_providers:whatsapp_status",
            kwargs={"team_slug": team_with_users.slug, "pk": other_provider.pk},
        )

        assert authed_client.get(url).status_code == 404


@pytest.mark.django_db()
class TestWhatsappRefresh:
    """One button, one task: the numbers and the template are re-fetched together."""

    def test_queues_a_refresh(self, meta_provider, authed_client, django_capture_on_commit_callbacks):
        with (
            mock.patch("apps.service_providers.tasks.sync_whatsapp_provider_task.delay") as delay,
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = authed_client.post(_whatsapp_url("whatsapp_refresh", meta_provider))

        assert response.status_code == 200
        delay.assert_called_once_with(meta_provider.pk)
        meta_provider.refresh_from_db()
        assert meta_provider.whatsapp_refresh_info["started_at"]

    def test_does_not_queue_a_second_refresh_while_one_is_running(
        self, meta_provider, authed_client, django_capture_on_commit_callbacks
    ):
        meta_provider.mark_whatsapp_refresh_queued()

        with (
            mock.patch("apps.service_providers.tasks.sync_whatsapp_provider_task.delay") as delay,
            django_capture_on_commit_callbacks(execute=True),
        ):
            authed_client.post(_whatsapp_url("whatsapp_refresh", meta_provider))

        delay.assert_not_called()

    def test_queues_a_refresh_when_the_running_one_has_stalled(
        self, meta_provider, authed_client, django_capture_on_commit_callbacks
    ):
        stalled = timezone.now() - timedelta(minutes=30)
        meta_provider.extra_data["whatsapp_refresh"] = {"started_at": stalled.isoformat()}
        meta_provider.save()

        with (
            mock.patch("apps.service_providers.tasks.sync_whatsapp_provider_task.delay") as delay,
            django_capture_on_commit_callbacks(execute=True),
        ):
            authed_client.post(_whatsapp_url("whatsapp_refresh", meta_provider))

        delay.assert_called_once_with(meta_provider.pk)


@pytest.mark.django_db()
class TestWhatsappTestSend:
    def _post(self, client, provider, **overrides):
        data = {
            "from_number_id": "1020671484465717",
            "to_number": "+27 82 123 4567",
            "message": "Checking in from Open Chat Studio.",
        }
        data.update(overrides)
        return client.post(_whatsapp_url("whatsapp_send_test", provider), data=data)

    def test_sends_using_the_cached_phone_number_id(self, meta_provider, authed_client):
        with mock.patch.object(MetaCloudAPIService, "send_template_message") as send:
            response = self._post(authed_client, meta_provider)

        assert response.status_code == 200
        send.assert_called_once_with(
            message="Checking in from Open Chat Studio.",
            from_="1020671484465717",
            to="+27821234567",
            platform=ChannelPlatform.WHATSAPP,
        )
        assert "+27821234567" in response.content.decode()

    def test_shows_what_meta_said_when_it_rejects_the_message(self, meta_provider, authed_client):
        body = '{"error": {"message": "Template name does not exist", "code": 132001}}'
        error = httpx.HTTPStatusError(
            "bad request",
            request=httpx.Request("POST", "https://test"),
            response=httpx.Response(400, text=body, request=httpx.Request("POST", "https://test")),
        )
        with mock.patch.object(MetaCloudAPIService, "send_template_message", side_effect=error):
            response = self._post(authed_client, meta_provider)

        content = response.content.decode()
        assert response.status_code == 200
        assert "400" in content
        assert "132001" in content

    def test_shows_the_service_window_message(self, meta_provider, authed_client):
        error = ServiceWindowExpiredException("The 'new_bot_message' template was not found.")
        with mock.patch.object(MetaCloudAPIService, "send_template_message", side_effect=error):
            response = self._post(authed_client, meta_provider)

        assert "template was not found" in response.content.decode()

    def test_rejects_a_number_that_is_not_a_phone_number(self, meta_provider, authed_client):
        with mock.patch.object(MetaCloudAPIService, "send_template_message") as send:
            response = self._post(authed_client, meta_provider, to_number="nope")

        send.assert_not_called()
        assert "valid phone number" in response.content.decode()

    def test_rejects_a_sender_that_is_not_on_the_account(self, meta_provider, authed_client):
        with mock.patch.object(MetaCloudAPIService, "send_template_message") as send:
            response = self._post(authed_client, meta_provider, from_number_id="999")

        send.assert_not_called()
        assert response.status_code == 200


@pytest.mark.django_db()
class TestWhatsappTestFormAvailability:
    """The test send needs a usable template, so the form follows the cached check."""

    def test_form_is_enabled_when_the_template_is_usable(self, meta_provider, authed_client):
        _cache_template(meta_provider, ok=True, template={"status": "APPROVED"})

        response = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider))

        assert response.context["template_ok"] is True
        assert "Send test message" in response.content.decode()

    @pytest.mark.parametrize(
        "status",
        [
            pytest.param({"problems": ["No template named 'new_bot_message'"]}, id="problems"),
            pytest.param({"error": "(#190) Error validating access token"}, id="api_error"),
            pytest.param(None, id="never_checked"),
        ],
    )
    def test_form_is_disabled_when_the_template_is_not_usable(self, meta_provider, authed_client, status):
        if status is not None:
            _cache_template(meta_provider, **status)

        response = authed_client.get(_whatsapp_url("whatsapp_status", meta_provider))

        content = response.content.decode()
        assert response.context["template_ok"] is False
        assert re.search(r"<fieldset[^>]*\sdisabled", content)
        assert "Test messages can only be sent once the template above is available." in content

    def test_a_refresh_keeps_the_form_enabled(self, meta_provider, authed_client):
        """The refresh re-renders from the cache, so the form does not flicker back to disabled."""
        _cache_template(meta_provider, ok=True, template={"status": "APPROVED"})

        with mock.patch("apps.service_providers.tasks.sync_whatsapp_provider_task.delay"):
            response = authed_client.post(_whatsapp_url("whatsapp_refresh", meta_provider))

        assert response.context["template_ok"] is True
