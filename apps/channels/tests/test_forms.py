from unittest.mock import PropertyMock, patch

import pytest
from django.forms.widgets import HiddenInput, Select

from apps.channels.forms import ChannelForm, WhatsappChannelForm
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
def test_channel_form_reveals_provider_types(team_with_users, platform, expected_widget_cls):
    """Test that the message provider field is being hidden when not applicable to a certain platform"""
    # First create a messaging provider
    message_provider = MessagingProviderFactory(type=MessagingProviderType("twilio"), team=team_with_users)
    MessagingProviderFactory(type=MessagingProviderType("twilio"))

    form = ChannelForm(initial={"platform": ChannelPlatform(platform)}, team=team_with_users)
    widget = form.fields["messaging_provider"].widget
    assert isinstance(widget, expected_widget_cls)

    form_queryset = form.fields["messaging_provider"].queryset
    assert form_queryset.count() == MessagingProvider.objects.filter(team=team_with_users).count()
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
