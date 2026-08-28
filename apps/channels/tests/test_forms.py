import json
from unittest.mock import Mock, PropertyMock, patch

import httpx
import pytest
from django.forms.widgets import HiddenInput, Select

from apps.channels.forms import ChannelForm, SlackChannelForm, TelegramChannelForm, WhatsappChannelForm
from apps.channels.models import ChannelPlatform
from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.parametrize(
    ("platform", "expected_widget_cls"),
    [
        ("whatsapp", Select),
        ("telegram", HiddenInput),
    ],
)
def test_channel_form_reveals_provider_types(experiment, platform, expected_widget_cls):
    """Test that the message provider field is being hidden when not applicable to a certain platform"""
    # First create a messaging provider
    message_provider = MessagingProviderFactory.create(type=MessagingProviderType("twilio"), team=experiment.team)
    MessagingProviderFactory.create(type=MessagingProviderType("twilio"))

    form = ChannelForm(initial={"platform": ChannelPlatform(platform)}, experiment=experiment)
    widget = form.fields["messaging_provider"].widget
    assert isinstance(widget, expected_widget_cls)

    form_queryset = form.fields["messaging_provider"].queryset
    assert form_queryset.count() == MessagingProvider.objects.filter(team=experiment.team).count()
    assert form_queryset.first() == message_provider


@pytest.mark.parametrize(
    ("number", "is_valid"),
    [
        ("+27812345678", True),
        ("0812345678", False),
        ("+27 81 234 5678", True),
        ("+27-81-234-5678", True),
        ("+27-81 2345678", True),
        ("+27_81_234_5678", False),
        ("0800 100 030", False),
        ("+32 (0)27888484", True),
        # Parses cleanly but is not a number anyone can be reached on
        ("+1234", False),
        ("+10000000000", False),
    ],
)
@patch("apps.channels.forms.WhatsappChannelForm.messaging_provider")
def test_whatsapp_form_validates_number_format(experiment, number, is_valid):
    form = WhatsappChannelForm(experiment=experiment, data={"number": number})
    assert form.is_valid() == is_valid
    if not is_valid:
        assert form.errors["number"] == ["Enter a valid phone number (e.g. +12125552368)."]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("provider_type", "number", "number_found_at_provider"),
    [
        (MessagingProviderType.twilio, "+12125552368", True),
        (MessagingProviderType.twilio, "+12125552333", False),
        # Turnio doesn't have a way to list account numbers, so assume it's always valid
        (MessagingProviderType.turnio, "+12125552368", True),
        (MessagingProviderType.turnio, "+12125552333", True),
    ],
)
@patch("apps.channels.forms.ExtraFormBase.messaging_provider", new_callable=PropertyMock)
@patch("apps.service_providers.messaging_service.TwilioService._get_account_numbers")
def test_whatsapp_form_checks_number(
    _get_account_numbers, messaging_provider, provider_type, number, number_found_at_provider, experiment
):
    _get_account_numbers.return_value = ["+12125552368"]
    provider = MessagingProviderFactory.create(type=provider_type, config={"account_sid": "123", "auth_token": "123"})
    messaging_provider.return_value = provider
    form = WhatsappChannelForm(experiment=experiment, data={"number": number, "messaging_provider": provider.id})
    if number_found_at_provider:
        assert form.is_valid(), f"Form errors: {form.errors}"
    else:
        assert form.errors["number"][0] == (
            f"{number} was not found at the provider. Please make sure it is there before proceeding"
        )


@pytest.mark.django_db()
@patch("apps.channels.forms.ExtraFormBase.messaging_provider", new_callable=PropertyMock)
@patch("apps.service_providers.messaging_service.httpx.get")
def test_whatsapp_form_meta_cloud_api_resolves_phone_number_id(mock_httpx_get, messaging_provider, experiment):
    """Test that the phone number ID is fetched from Meta API and stored in extra_data"""

    mock_httpx_get.return_value = httpx.Response(
        200,
        json={
            "data": [
                {"id": "12345", "display_phone_number": "+1 (212) 555-2368"},
            ]
        },
        request=httpx.Request("GET", "https://test"),
    )
    provider = MessagingProviderFactory.create(
        type=MessagingProviderType.meta_cloud_api,
        config={"access_token": "test_token", "business_id": "biz_123"},
    )
    messaging_provider.return_value = provider

    form = WhatsappChannelForm(
        experiment=experiment, data={"number": "+12125552368", "messaging_provider": provider.id}
    )
    assert form.is_valid(), f"Form errors: {form.errors}"

    # ChannelFormWrapper.save() passes extra_form.cleaned_data as channel.extra_data,
    # so phone_number_id should be in cleaned_data directly.
    assert form.cleaned_data["phone_number_id"] == "12345"
    assert form.cleaned_data["number"] == "+12125552368"


# Slack channel keyword uniqueness tests
@pytest.mark.django_db()
def test_slack_channel_new_with_keywords_succeeds(team_with_users, experiment):
    """Test creating a new Slack channel with keywords succeeds"""

    # Create messaging provider
    provider = MessagingProviderFactory.create(type=MessagingProviderType.slack, team=team_with_users)

    # Mock the messaging service
    mock_service = Mock()
    mock_service.get_channel_by_name.return_value = None  # Not using specific channel

    with patch.object(provider, "get_messaging_service", return_value=mock_service):
        form_data = {
            "channel_scope": "all",
            "routing_method": "keywords",
            "keywords": "health, benefits, support",
            "messaging_provider": provider.id,
        }

        form = SlackChannelForm(experiment=experiment, data=form_data)
        form.messaging_provider = provider

        assert form.is_valid(), f"Form errors: {form.errors}"

        cleaned_data = form.cleaned_data
        assert cleaned_data["keywords"] == ["health", "benefits", "support"]
        assert cleaned_data["slack_channel_id"] == "*"
        assert not cleaned_data["is_default"]


@pytest.mark.django_db()
def test_slack_channel_edit_keeping_some_keywords_succeeds(team_with_users, experiment):
    """Test editing existing channel keeping some keywords succeeds"""

    # Create messaging provider
    provider = MessagingProviderFactory.create(type=MessagingProviderType.slack, team=team_with_users)

    # Create the channel we want to edit - this simulates the Health Bot from browser
    health_bot = ExperimentChannelFactory.create(
        team=team_with_users,
        platform=ChannelPlatform.SLACK,
        messaging_provider=provider,
        name="Health Bot",
        extra_data={
            "slack_channel_id": "*",
            "keywords": ["health", "benefits", "medical", "insurance", "deductible", "copay", "coverage"],
            "is_default": False,
        },
    )

    # Mock the messaging service
    mock_service = Mock()
    mock_service.get_channel_by_name.return_value = None

    with patch.object(provider, "get_messaging_service", return_value=mock_service):
        # Simulate editing the Health Bot to reduce keywords but keep some existing ones
        form_data = {
            "channel_scope": "all",
            "routing_method": "keywords",
            "keywords": "health, benefits, nutrition",  # Keep "health" and "benefits", add "nutrition" (no conflict)
            "messaging_provider": provider.id,
        }

        # This simulates the browser scenario - editing an existing channel
        form = SlackChannelForm(
            experiment=experiment, data=form_data, initial=health_bot.extra_data, channel=health_bot
        )
        form.messaging_provider = provider
        form.instance = health_bot  # This should be set by the channel parameter

        # This should succeed - editing a channel should allow keeping its own existing keywords
        assert form.is_valid(), f"Form errors: {form.errors}"

        cleaned_data = form.cleaned_data
        assert cleaned_data["keywords"] == ["health", "benefits", "nutrition"]


@pytest.mark.django_db()
def test_slack_channel_duplicate_keywords_fails(team_with_users, experiment):
    """Test creating new channel with existing keywords fails"""

    # Create messaging provider
    provider = MessagingProviderFactory.create(
        type=MessagingProviderType.slack, team=team_with_users, config={"slack_team_id": "123"}
    )

    # Create existing channel with keywords
    ExperimentChannelFactory.create(
        team=team_with_users,
        platform=ChannelPlatform.SLACK,
        messaging_provider=provider,
        name="Existing Bot",
        extra_data={"slack_channel_id": "*", "keywords": ["health", "benefits"], "is_default": False},
    )

    # Mock the messaging service
    mock_service = Mock()
    mock_service.get_channel_by_name.return_value = None

    with patch.object(provider, "get_messaging_service", return_value=mock_service):
        # Try to create new channel with overlapping keywords
        form_data = {
            "channel_scope": "all",
            "routing_method": "keywords",
            "keywords": "health, medical",  # "health" conflicts
            "messaging_provider": provider.id,
        }

        form = SlackChannelForm(experiment=experiment, data=form_data)
        form.messaging_provider = provider

        assert not form.is_valid()
        assert "keywords" in form.errors


@pytest.mark.django_db()
def test_slack_channel_cross_team_keyword_conflicts(team_with_users, experiment):
    """Test that keyword conflicts are validated system-wide across teams"""

    # Create messaging provider
    provider = MessagingProviderFactory.create(
        type=MessagingProviderType.slack, team=team_with_users, config={"slack_team_id": "123"}
    )

    # Create a different team that shares the same Slack workspace (same messaging provider)
    other_team = TeamWithUsersFactory.create()

    # Create existing channel in the OTHER team with keywords
    ExperimentChannelFactory.create(
        team=other_team,  # Different team!
        platform=ChannelPlatform.SLACK,
        messaging_provider=provider,  # Same messaging provider (same Slack workspace)
        name="Other Team Bot",
        extra_data={"slack_channel_id": "*", "keywords": ["health", "benefits"], "is_default": False},
    )

    # Mock the messaging service
    mock_service = Mock()
    mock_service.get_channel_by_name.return_value = None

    with patch.object(provider, "get_messaging_service", return_value=mock_service):
        # Try to create new channel in current team with conflicting keywords
        form_data = {
            "channel_scope": "all",
            "routing_method": "keywords",
            "keywords": "health, wellness",  # "health" conflicts with other team's bot
            "messaging_provider": provider.id,
        }

        form = SlackChannelForm(experiment=experiment, data=form_data)
        form.messaging_provider = provider

        # Should fail because keywords must be unique across ALL teams using the same Slack workspace
        assert not form.is_valid(), f"Form errors: {form.errors}"

        error_message = str(form.errors)
        assert "Other Team Bot" not in error_message
        assert "health" in error_message


# WhatsApp webhook auto-configuration tests
@pytest.mark.django_db()
@patch("apps.channels.forms.ExtraFormBase.messaging_provider", new_callable=PropertyMock)
@patch("apps.service_providers.messaging_service.TwilioService.set_incoming_webhook")
def test_whatsapp_post_save_configures_twilio_webhook(set_incoming_webhook, messaging_provider, experiment):
    provider = MessagingProviderFactory.create(
        type=MessagingProviderType.twilio, config={"account_sid": "123", "auth_token": "123"}
    )
    messaging_provider.return_value = provider
    channel = ExperimentChannelFactory(
        experiment=experiment,
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=provider,
        extra_data={"number": "+12125552368"},
    )
    form = WhatsappChannelForm(experiment=experiment, data={"number": "+12125552368"})

    form.post_save(channel)

    set_incoming_webhook.assert_called_once_with(channel.extra_data, channel.webhook_url)
    assert form.success_message == "Webhook configured automatically."
    assert form.warning_message == ""


@pytest.mark.django_db()
@patch("apps.channels.forms.ExtraFormBase.messaging_provider", new_callable=PropertyMock)
@patch("apps.service_providers.messaging_service.TwilioService.set_incoming_webhook")
def test_whatsapp_post_save_falls_back_to_manual_instructions_on_failure(
    set_incoming_webhook, messaging_provider, experiment
):
    set_incoming_webhook.side_effect = ValueError("No WhatsApp sender found for +12125552368")
    provider = MessagingProviderFactory.create(
        type=MessagingProviderType.twilio, config={"account_sid": "123", "auth_token": "123"}
    )
    messaging_provider.return_value = provider
    channel = ExperimentChannelFactory(
        experiment=experiment,
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=provider,
        extra_data={"number": "+12125552368"},
    )
    form = WhatsappChannelForm(experiment=experiment, data={"number": "+12125552368"})

    form.post_save(channel)

    assert channel.webhook_url in form.warning_message
    assert form.success_message == ""


@pytest.mark.django_db()
@patch("apps.channels.forms.ExtraFormBase.messaging_provider", new_callable=PropertyMock)
def test_whatsapp_post_save_shows_manual_instructions_for_other_providers(messaging_provider, experiment):
    provider = MessagingProviderFactory.create(type=MessagingProviderType.turnio, config={"auth_token": "123"})
    messaging_provider.return_value = provider
    channel = ExperimentChannelFactory(
        experiment=experiment,
        platform=ChannelPlatform.WHATSAPP,
        messaging_provider=provider,
        extra_data={"number": "+12125552368"},
    )
    form = WhatsappChannelForm(experiment=experiment, data={"number": "+12125552368"})

    form.post_save(channel)

    assert form.success_message == f"Use the following URL when setting up the webhook: {channel.webhook_url}"


@pytest.mark.django_db()
@patch("apps.channels.webhooks.TelegramWebhookManager.set_incoming_webhook")
def test_telegram_post_save_configures_webhook(set_incoming_webhook, experiment):
    channel = ExperimentChannelFactory(
        experiment=experiment, platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "tok"}
    )
    form = TelegramChannelForm(experiment=experiment, data={"bot_token": "tok"})

    form.post_save(channel)

    set_incoming_webhook.assert_called_once_with(channel.extra_data, channel.webhook_url)
    assert form.success_message == "Webhook configured automatically."
    assert form.warning_message == ""


@pytest.mark.django_db()
@patch("apps.channels.webhooks.TelegramWebhookManager.set_incoming_webhook")
def test_telegram_post_save_falls_back_to_warning_on_failure(set_incoming_webhook, experiment):
    set_incoming_webhook.side_effect = Exception("Telegram is down")
    channel = ExperimentChannelFactory(
        experiment=experiment, platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "tok"}
    )
    form = TelegramChannelForm(experiment=experiment, data={"bot_token": "tok"})

    form.post_save(channel)

    assert channel.webhook_url in form.warning_message
    assert form.success_message == ""


@pytest.mark.django_db()
class TestChannelEnabledToggle:
    """The disable switch and its optional static message (issue #4200)."""

    def _form(self, experiment, channel, **data):
        form_data = {"name": channel.name, "platform": channel.platform, **data}
        return ChannelForm(experiment=experiment, instance=channel, data=form_data)

    def test_new_channels_start_enabled(self, experiment):
        form = ChannelForm(initial={"platform": ChannelPlatform.TELEGRAM}, experiment=experiment)
        assert form.initial.get("enabled") is not False
        assert '"channelEnabled": true' in form.form_attrs["x-data"]

    def test_disabling_saves_the_static_message(self, experiment):
        channel = ExperimentChannelFactory.create(experiment=experiment, team=experiment.team)
        form = self._form(experiment, channel, disabled_message="Back on Monday")

        assert form.is_valid(), form.errors
        form.save(experiment, config_data=channel.extra_data)

        channel.refresh_from_db()
        assert channel.enabled is False
        assert channel.disabled_message == "Back on Monday"

    def test_disabling_without_a_message_is_allowed(self, experiment):
        channel = ExperimentChannelFactory.create(experiment=experiment, team=experiment.team)
        form = self._form(experiment, channel)

        assert form.is_valid(), form.errors
        form.save(experiment, config_data=channel.extra_data)

        channel.refresh_from_db()
        assert channel.enabled is False
        assert channel.disabled_message == ""

    def test_re_enabling_a_channel(self, experiment):
        channel = ExperimentChannelFactory.create(
            experiment=experiment, team=experiment.team, enabled=False, disabled_message="Back on Monday"
        )
        form = self._form(experiment, channel, enabled="on")

        assert form.is_valid(), form.errors
        form.save(experiment, config_data=channel.extra_data)

        channel.refresh_from_db()
        assert channel.enabled is True

    def test_alpine_state_follows_a_disabled_channel(self, experiment):
        channel = ExperimentChannelFactory.create(experiment=experiment, team=experiment.team, enabled=False)
        form = ChannelForm(experiment=experiment, instance=channel)
        assert '"channelEnabled": false' in form.form_attrs["x-data"]


NUMBER_A = {
    "phone_number_id": "1020671484465717",
    "number": "+27647084804",
    "display": "+27 64 708 4804",
    "verified_name": "TenantHive",
}
NUMBER_B = {
    "phone_number_id": "9938471029384",
    "number": "+27825550134",
    "display": "+27 82 555 0134",
    "verified_name": "TenantHive Support",
}


def _meta_provider(team, numbers=None):
    extra_data = {"verify_token_hash": "abc"}
    if numbers is not None:
        extra_data["whatsapp_numbers"] = {"state": "ok", "numbers": numbers}
    return MessagingProviderFactory.create(
        team=team,
        type=MessagingProviderType.meta_cloud_api,
        config={"access_token": "token", "business_id": "biz_123"},
        extra_data=extra_data,
    )


def _numbers_by_provider(form):
    return json.loads(form.form_attrs["x-data"])["numbersByProvider"]


@pytest.mark.django_db()
class TestWhatsappNumberOptions:
    """Every provider's cached numbers are rendered up front, so switching provider needs no request."""

    def test_cached_numbers_are_rendered_for_every_provider(self, experiment):
        synced = _meta_provider(experiment.team, [NUMBER_A, NUMBER_B])
        never_synced = _meta_provider(experiment.team)

        options = _numbers_by_provider(WhatsappChannelForm(experiment=experiment))

        assert options[str(synced.id)]["numbers"] == [
            {"value": "+27647084804", "label": "+27 64 708 4804 - TenantHive"},
            {"value": "+27825550134", "label": "+27 82 555 0134 - TenantHive Support"},
        ]
        assert options[str(never_synced.id)]["numbers"] == []
        assert options[str(synced.id)]["provider_url"].endswith(f"/messaging/{synced.id}/")

    def test_a_saved_number_that_is_not_cached_is_still_offered(self, experiment):
        """Editing a channel must not force a number change just because Meta no longer lists it."""
        provider = _meta_provider(experiment.team, [NUMBER_A])
        channel = ExperimentChannelFactory(
            experiment=experiment,
            platform=ChannelPlatform.WHATSAPP,
            messaging_provider=provider,
            extra_data={"number": "+27821110000", "phone_number_id": "555"},
        )

        options = _numbers_by_provider(WhatsappChannelForm(experiment=experiment, channel=channel))

        assert [option["value"] for option in options[str(provider.id)]["numbers"]] == [
            "+27647084804",
            "+27821110000",
        ]

    def test_providers_of_other_types_are_not_listed(self, experiment):
        twilio = MessagingProviderFactory.create(team=experiment.team, type=MessagingProviderType.twilio)

        options = _numbers_by_provider(WhatsappChannelForm(experiment=experiment))

        assert str(twilio.id) not in options


@pytest.mark.django_db()
class TestWhatsappNumberValidation:
    def _form(self, experiment, provider, number="+27647084804"):
        return WhatsappChannelForm(experiment=experiment, data={"number": number, "messaging_provider": provider.id})

    def test_a_cached_number_resolves_its_id_without_calling_meta(self, experiment):
        provider = _meta_provider(experiment.team, [NUMBER_A])

        with patch.object(MessagingProvider, "get_messaging_service") as get_service:
            form = self._form(experiment, provider)
            assert form.is_valid(), form.errors

        get_service.assert_not_called()
        assert form.cleaned_data["phone_number_id"] == "1020671484465717"

    def test_a_number_missing_from_a_cached_provider_is_rejected(self, experiment):
        provider = _meta_provider(experiment.team, [NUMBER_A])

        form = self._form(experiment, provider, number="+27829990000")

        assert not form.is_valid()
        assert "was not found at the provider" in form.errors["number"][0]

    def test_an_empty_cache_is_populated_on_save(self, experiment):
        """The number check doubles as the trigger that fills a provider's number cache."""
        provider = _meta_provider(experiment.team)
        service = Mock()
        service.get_phone_numbers.return_value = [NUMBER_A, NUMBER_B]

        with patch.object(MessagingProvider, "get_messaging_service", return_value=service):
            form = self._form(experiment, provider)
            assert form.is_valid(), form.errors

        assert form.cleaned_data["phone_number_id"] == "1020671484465717"
        provider.refresh_from_db()
        assert provider.whatsapp_numbers == [NUMBER_A, NUMBER_B]

    def test_an_unknown_number_leaves_the_cache_populated_for_the_re_render(self, experiment):
        provider = _meta_provider(experiment.team)
        service = Mock()
        service.get_phone_numbers.return_value = [NUMBER_A]

        with patch.object(MessagingProvider, "get_messaging_service", return_value=service):
            form = self._form(experiment, provider, number="+27829990000")
            assert not form.is_valid()

            assert "was not found at the provider" in form.errors["number"][0]
            # the re-rendered form offers what the sync found
            assert _numbers_by_provider(form)[str(provider.id)]["numbers"] == [
                {"value": "+27647084804", "label": "+27 64 708 4804 - TenantHive"}
            ]

    def test_a_failed_sync_asks_the_user_to_try_again(self, experiment):
        provider = _meta_provider(experiment.team)
        service = Mock()
        service.get_phone_numbers.side_effect = httpx.HTTPError("boom")

        with patch.object(MessagingProvider, "get_messaging_service", return_value=service):
            form = self._form(experiment, provider)

        assert not form.is_valid()
        assert form.errors["number"] == ["Could not validate this number right now. Please try again."]
