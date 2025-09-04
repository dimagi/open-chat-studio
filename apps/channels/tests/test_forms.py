from unittest.mock import PropertyMock, patch

import pytest
from django import forms
from django.forms.widgets import HiddenInput, Select

from apps.channels.forms import ChannelForm, SlackChannelForm, WhatsappChannelForm
from apps.channels.models import ChannelPlatform
from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.utils.factories.service_provider_factories import MessagingProviderFactory


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
def test_whatsapp_form_validates_number_format(messaging_provider, number, is_valid):
    form = WhatsappChannelForm({"number": number})
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
    _get_account_numbers, messaging_provider, provider_type, number, number_found_at_provider
):
    _get_account_numbers.return_value = ["+12125552368"]
    provider = MessagingProviderFactory(type=provider_type, config={"account_sid": "123", "auth_token": "123"})
    messaging_provider.return_value = provider
    form = WhatsappChannelForm({"number": number, "messaging_provider": provider.id})
    assert form.is_valid()
    if not number_found_at_provider:
        assert form.warning_message == (
            f"{number} was not found at the provider. Please make sure it is there before proceeding"
        )


# Slack form validation tests - focusing on keyword parsing only
@pytest.mark.parametrize(
    ("keywords_input", "is_valid", "expected_count"),
    [
        ("health, benefits, medical", True, 3),
        ("health,benefits,medical", True, 3),  # No spaces
        ("health, benefits, medical, support, hr", True, 5),  # Max allowed
        ("health, benefits, medical, support, hr, extra", False, 0),  # Too many
        ("health", True, 1),  # Single keyword
    ],
)
def test_slack_form_keyword_parsing(keywords_input, is_valid, expected_count):
    """Test keyword parsing and count validation (max 5)"""
    form = SlackChannelForm()
    form.cleaned_data = {"keywords": keywords_input}

    if is_valid:
        result = form.clean_keywords()
        assert len(result) == expected_count
    else:
        with pytest.raises(forms.ValidationError) as exc_info:
            form.clean_keywords()
        assert "Too many keywords" in str(exc_info.value)


@pytest.mark.parametrize(
    ("keyword", "is_valid"),
    [
        ("health", True),
        ("health-care", True),  # Hyphens allowed
        ("health-care2", True),  # Hyphens and numbers allowed
        ("health123", True),  # Numbers allowed
        ("health_care", False),  # Underscores not allowed
        ("health care", False),  # Spaces not allowed (single-word keywords only)
        ("health@care", False),  # Special chars not allowed
        ("a" * 25, True),  # Max length
        ("a" * 26, False),  # Too long
        ("   ", True),  # Empty after strip becomes empty list
    ],
)
def test_slack_form_keyword_character_validation(keyword, is_valid):
    """Test keyword character and length validation"""
    form = SlackChannelForm()
    form.cleaned_data = {"keywords": keyword}

    if is_valid:
        result = form.clean_keywords()
        if keyword.strip():  # Non-empty keyword
            assert len(result) == 1
            assert result[0] == keyword.lower()
        else:  # Empty keyword becomes empty list
            assert len(result) == 0
    else:
        with pytest.raises(forms.ValidationError):
            form.clean_keywords()


def test_slack_form_duplicate_keywords_validation():
    """Test that duplicate keywords are rejected"""
    form = SlackChannelForm()
    form.cleaned_data = {"keywords": "health, benefits, health"}  # Duplicate "health"

    with pytest.raises(forms.ValidationError) as exc_info:
        form.clean_keywords()
    assert "Duplicate keywords are not allowed" in str(exc_info.value)
