import pytest

from apps.admin.forms import OcsConfigurationForm
from apps.admin.models import OcsConfiguration
from apps.channels.models import ChannelPlatform
from apps.channels.utils import clear_widget_embed_key_cache, get_widget_embed_key
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory


def form_data(**overrides):
    data = {
        "chat_widget_enabled": True,
        "chatbot_id": "",
        "button_text": "Ask me!",
        "welcome_messages": "Hi!",
        "starter_questions": "How do I create a bot?",
        "position": "right",
    }
    data.update(overrides)
    return data


@pytest.fixture()
def widget_channel():
    channel = ExperimentChannelFactory.create(
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"widget_token": "site-widget-token", "allowed_domains": ["example.com"]},
    )
    chatbot_id = str(channel.experiment.public_id)
    clear_widget_embed_key_cache(chatbot_id)
    yield channel
    clear_widget_embed_key_cache(chatbot_id)


@pytest.mark.django_db()
class TestOcsConfigurationFormChatbotId:
    """The site widget authorizes itself with the chatbot's embed key, so a chatbot without one
    would render a widget that cannot start a session."""

    def test_accepts_a_chatbot_with_a_widget_channel(self, widget_channel):
        form = OcsConfigurationForm(data=form_data(chatbot_id=str(widget_channel.experiment.public_id)))
        assert form.is_valid(), form.errors

    def test_accepts_an_empty_chatbot_id(self):
        form = OcsConfigurationForm(data=form_data())
        assert form.is_valid(), form.errors

    def test_rejects_a_chatbot_without_a_widget_channel(self):
        experiment = ExperimentFactory.create()
        form = OcsConfigurationForm(data=form_data(chatbot_id=str(experiment.public_id)))
        assert not form.is_valid()
        assert "No embedded widget channel" in form.errors["chatbot_id"][0]

    def test_rejects_an_unknown_chatbot_id(self):
        form = OcsConfigurationForm(data=form_data(chatbot_id="not-a-chatbot"))
        assert not form.is_valid()
        assert "No embedded widget channel" in form.errors["chatbot_id"][0]

    def test_saving_refreshes_the_cached_embed_key(self, widget_channel):
        """A freshly configured chatbot must not be shadowed by a cached miss."""
        chatbot_id = str(widget_channel.experiment.public_id)
        assert get_widget_embed_key(chatbot_id) == "site-widget-token"
        widget_channel.extra_data["widget_token"] = "rotated-token"
        widget_channel.save()

        form = OcsConfigurationForm(data=form_data(chatbot_id=chatbot_id))
        assert form.is_valid(), form.errors
        form.save()

        assert get_widget_embed_key(chatbot_id) == "rotated-token"
        assert OcsConfiguration.objects.get().config.chat_widget.chatbot_id == chatbot_id
