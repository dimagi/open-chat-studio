import pytest
from django.forms.widgets import HiddenInput, Select

from apps.channels.forms import ChannelForm
from apps.channels.models import ChannelPlatform
from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.utils.factories.service_provider_factories import MessagingProviderFactory


@pytest.mark.parametrize("platform,", ["whatsapp", "telegram"])
def test_channel_form_reveals_provider_types(team, platform):
    """Test that the message provider field is being hidden when not applicable to a certain platform"""
    # First create a messaging provider
    message_provider = MessagingProviderFactory(type=MessagingProviderType("twilio"), team=team)
    MessagingProviderFactory(type=MessagingProviderType("twilio"))

    form = ChannelForm(initial={"platform": ChannelPlatform(platform)}, team=team)
    widget = form.fields["messaging_provider"].widget
    form_queryset = form.fields["messaging_provider"].queryset
    if platform == "whatsapp":
        assert isinstance(widget, Select)
    else:
        assert isinstance(widget, HiddenInput)

    assert form_queryset.count() == MessagingProvider.objects.filter(team=team).count()
    assert form_queryset.first() == message_provider
