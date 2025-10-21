import json
from typing import Self

from django.core.cache import cache
from django.db import models
from django.db.models.signals import pre_delete, pre_save
from django_pydantic_field import SchemaField
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

SITE_CONFIG_CACHE_KEY = "ocs_site_config"
SITE_CONFIG_CACHE_TIMEOUT = 60 * 60 * 24


class ChatWidgetConfig(PydanticBaseModel):
    """Configuration schema for the chat widget."""

    enabled: bool = Field(default=False)
    chatbot_id: str = Field(default="", description="ID of the chatbot to use")
    button_text: str = Field(default="Ask me!", description="Text displayed on the chat button")
    welcome_messages: list[str] = Field(
        default=["Hi! Welcome to our support chat.", "How can we help you today?"],
        description="Welcome messages to display",
    )
    starter_questions: list[str] = Field(
        default=["How do I create a bot?", "How do I connect my bot to WhatsApp?"],
        description="Starter questions to show users",
    )
    position: str = Field(default="right", description="Position of the widget")

    def get_widget_attributes(self):
        attrs = {
            "chatbot-id": self.chatbot_id,
            "button-text": self.button_text,
            "welcome-messages": json.dumps(self.welcome_messages),
            "starter-questions": json.dumps(self.starter_questions),
            "position": self.position,
        }
        return attrs


class SiteConfig(PydanticBaseModel):
    chat_widget: ChatWidgetConfig = Field(default_factory=ChatWidgetConfig)


class OcsConfiguration(models.Model):
    """Model to store site-wide configuration settings."""

    config = SchemaField(schema=SiteConfig, help_text="Configuration data")

    def __str__(self):
        return "Site config"

    @classmethod
    def get_instance(cls) -> Self | None:
        return cls.objects.first()


def get_site_config() -> SiteConfig:
    """Get the site configuration, using Django's cache for performance."""
    cached_config = cache.get(SITE_CONFIG_CACHE_KEY)
    if cached_config is not None:
        return cached_config

    config_obj = OcsConfiguration.objects.first()
    if config_obj:
        site_config = config_obj.config
        cache.set(SITE_CONFIG_CACHE_KEY, site_config, SITE_CONFIG_CACHE_TIMEOUT)
        return site_config

    default_config = SiteConfig()
    cache.set(SITE_CONFIG_CACHE_KEY, default_config, 300)  # 5 minutes
    return default_config


def clear_site_config_cache(**kwargs):
    cache.delete(SITE_CONFIG_CACHE_KEY)


pre_save.connect(clear_site_config_cache, sender=OcsConfiguration)
pre_delete.connect(clear_site_config_cache, sender=OcsConfiguration)
