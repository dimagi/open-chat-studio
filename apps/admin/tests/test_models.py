import pytest
from django.core.cache import cache

from apps.admin.models import (
    SITE_CONFIG_CACHE_KEY,
    ChatWidgetConfig,
    OcsConfiguration,
    SiteConfig,
    clear_site_config_cache,
    get_site_config,
)
from apps.channels.models import ChannelPlatform
from apps.channels.utils import clear_widget_embed_key_cache
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory


class TestChatWidgetConfig:
    def test_default_values(self):
        config = ChatWidgetConfig()
        assert config.enabled is False
        assert config.chatbot_id == ""
        assert config.button_text == "Ask me!"
        assert config.welcome_messages == ["Hi! Welcome to our support chat.", "How can we help you today?"]
        assert config.starter_questions == ["How do I create a bot?", "How do I connect my bot to WhatsApp?"]
        assert config.position == "right"

    def test_get_widget_attributes_escapes_json(self):
        config = ChatWidgetConfig(
            welcome_messages=['Message with "quotes" and \\backslashes'], starter_questions=["Question with 'quotes'"]
        )
        attrs = config.get_widget_attributes()
        assert attrs["welcome-messages"] == '["Message with \\"quotes\\" and \\\\backslashes"]'
        assert attrs["starter-questions"] == "[\"Question with 'quotes'\"]"

    def test_get_widget_attributes_without_a_chatbot_has_no_embed_key(self):
        assert "embed-key" not in ChatWidgetConfig().get_widget_attributes()

    @pytest.mark.django_db()
    def test_get_widget_attributes_includes_the_chatbots_embed_key(self):
        channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            extra_data={"widget_token": "site-widget-token", "allowed_domains": ["example.com"]},
        )
        chatbot_id = str(channel.experiment.public_id)
        clear_widget_embed_key_cache(chatbot_id)
        try:
            attrs = ChatWidgetConfig(chatbot_id=chatbot_id).get_widget_attributes()
        finally:
            clear_widget_embed_key_cache(chatbot_id)
        assert attrs["embed-key"] == "site-widget-token"

    @pytest.mark.django_db()
    def test_get_widget_attributes_omits_embed_key_when_the_chatbot_has_no_widget_channel(self):
        experiment = ExperimentFactory.create()
        chatbot_id = str(experiment.public_id)
        clear_widget_embed_key_cache(chatbot_id)
        try:
            attrs = ChatWidgetConfig(chatbot_id=chatbot_id).get_widget_attributes()
        finally:
            clear_widget_embed_key_cache(chatbot_id)
        assert "embed-key" not in attrs


class TestSiteConfig:
    def test_default_chat_widget(self):
        config = SiteConfig()
        assert isinstance(config.chat_widget, ChatWidgetConfig)
        assert config.chat_widget.enabled is False
        assert config.chat_widget.button_text == "Ask me!"


@pytest.fixture()
def clear_cache():
    clear_site_config_cache()
    try:
        yield
    finally:
        clear_site_config_cache()


@pytest.mark.django_db()
class TestOcsConfiguration:
    def test_get_config_with_existing_config(self, clear_cache):
        custom_site_config = SiteConfig(
            chat_widget=ChatWidgetConfig(enabled=True, button_text="Custom Button", chatbot_id="test-bot")
        )

        OcsConfiguration.objects.create(config=custom_site_config)

        retrieved_config = get_site_config()

        assert isinstance(retrieved_config, SiteConfig)
        assert retrieved_config.chat_widget.enabled is True
        assert retrieved_config.chat_widget.button_text == "Custom Button"
        assert retrieved_config.chat_widget.chatbot_id == "test-bot"

    def test_get_config_handles_none_in_cache_bug(self):
        OcsConfiguration.objects.all().delete()

        retrieved_config = get_site_config()

        assert isinstance(retrieved_config, SiteConfig)
        assert retrieved_config.chat_widget.enabled is False

    def test_save_calls_updates_cache(self):
        custom_site_config = SiteConfig(chat_widget=ChatWidgetConfig(button_text="Test Save"))
        OcsConfiguration.objects.create(config=custom_site_config)

        assert get_site_config().chat_widget.button_text == "Test Save"

        config = OcsConfiguration.objects.first()
        config.config.chat_widget.button_text = "Updated"
        config.save()

        assert get_site_config().chat_widget.button_text == "Updated"

    def test_django_cache_integration(self, clear_cache):
        custom_site_config = SiteConfig(
            chat_widget=ChatWidgetConfig(enabled=True, button_text="Cached Config", chatbot_id="cache-test")
        )
        OcsConfiguration.objects.create(config=custom_site_config)

        # First call should populate cache
        config1 = get_site_config()
        assert config1.chat_widget.button_text == "Cached Config"

        # Verify cache is populated
        cached_config = cache.get(SITE_CONFIG_CACHE_KEY)
        assert cached_config is not None
        assert cached_config.chat_widget.button_text == "Cached Config"

        # Deleting the object should reset the cache
        OcsConfiguration.objects.all().delete()
        cached_config = cache.get(SITE_CONFIG_CACHE_KEY)
        assert cached_config is None

        # Default value should now be returned
        config2 = get_site_config()
        assert config2.chat_widget.button_text == "Ask me!"
