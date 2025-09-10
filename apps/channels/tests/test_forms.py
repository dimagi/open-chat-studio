from unittest.mock import Mock, PropertyMock, patch

import pytest
from django.forms.widgets import HiddenInput, Select

from apps.channels.forms import ChannelForm, SlackChannelForm, WhatsappChannelForm
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
    message_provider = MessagingProviderFactory(type=MessagingProviderType("twilio"), team=experiment.team)
    MessagingProviderFactory(type=MessagingProviderType("twilio"))

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
    provider = MessagingProviderFactory(type=provider_type, config={"account_sid": "123", "auth_token": "123"})
    messaging_provider.return_value = provider
    form = WhatsappChannelForm(experiment=experiment, data={"number": number, "messaging_provider": provider.id})
    assert form.is_valid(), f"Form errors: {form.errors}"
    if not number_found_at_provider:
        assert form.warning_message == (
            f"{number} was not found at the provider. Please make sure it is there before proceeding"
        )


# Slack channel keyword uniqueness tests
@pytest.mark.django_db()
def test_slack_channel_new_with_keywords_succeeds(team_with_users, experiment):
    """Test creating a new Slack channel with keywords succeeds"""

    # Create messaging provider
    provider = MessagingProviderFactory(type=MessagingProviderType.slack, team=team_with_users)

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
    provider = MessagingProviderFactory(type=MessagingProviderType.slack, team=team_with_users)

    # Create the channel we want to edit - this simulates the Health Bot from browser
    health_bot = ExperimentChannelFactory(
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

    # Create another channel that would conflict if the exclusion logic is broken
    # This simulates having other channels with overlapping keywords
    ExperimentChannelFactory(
        team=team_with_users,
        platform=ChannelPlatform.SLACK,
        messaging_provider=provider,
        name="Other Bot",
        extra_data={"slack_channel_id": "*", "keywords": ["wellness", "fitness"], "is_default": False},
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
    provider = MessagingProviderFactory(
        type=MessagingProviderType.slack, team=team_with_users, config={"slack_team_id": "123"}
    )

    # Create existing channel with keywords
    ExperimentChannelFactory(
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


@pytest.mark.django_db()
def test_slack_channel_cross_team_keyword_conflicts(team_with_users, experiment):
    """Test that keyword conflicts are validated system-wide across teams"""

    # Create messaging provider
    provider = MessagingProviderFactory(
        type=MessagingProviderType.slack, team=team_with_users, config={"slack_team_id": "123"}
    )

    # Create a different team that shares the same Slack workspace (same messaging provider)
    other_team = TeamWithUsersFactory.create()

    # Create existing channel in the OTHER team with keywords
    ExperimentChannelFactory(
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

        # Error message should indicate which bot has the conflicting keywords
        error_message = str(form.errors)
        assert "Other Team Bot" in error_message
        assert "health" in error_message
