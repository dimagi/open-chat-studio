import pytest
from django.core.cache import cache
from django.test import override_settings

from apps.channels.models import ChannelPlatform
from apps.channels.utils import (
    clear_widget_embed_key_cache,
    fetch_widget_embed_key,
    get_allowed_email_domains,
    get_widget_embed_key,
    is_email_domain_allowed,
)
from apps.utils.factories.channels import ExperimentChannelFactory


class TestIsEmailDomainAllowed:
    @pytest.mark.parametrize(
        ("allowed_domains", "address", "expected"),
        [
            # Exact match.
            (["example.com", "*.foo.com"], "user@example.com", True),
            # Wildcard matches subdomain.
            (["example.com", "*.foo.com"], "user@mail.foo.com", True),
            # Wildcard does not match the bare domain.
            (["example.com", "*.foo.com"], "user@foo.com", False),
            # Domain not in allowlist.
            (["example.com"], "user@bar.com", False),
            # Empty setting => fail-closed (rejects everything).
            ([], "user@example.com", False),
            # Malformed address — no @.
            (["example.com"], "not-an-email", False),
            # Empty address.
            (["example.com"], "", False),
            # Case-insensitive match.
            (["example.com"], "user@Example.COM", True),
        ],
    )
    def test_matching(self, allowed_domains, address, expected):
        with override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=allowed_domains):
            assert is_email_domain_allowed(address) is expected


class TestGetAllowedEmailDomains:
    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["example.com", "*.foo.com"])
    def test_returns_list(self):
        assert get_allowed_email_domains() == ["example.com", "*.foo.com"]

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=[])
    def test_returns_empty_list_when_unset(self):
        assert get_allowed_email_domains() == []


@pytest.fixture()
def widget_channel():
    return ExperimentChannelFactory.create(
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"widget_token": "a-widget-token", "allowed_domains": ["example.com"]},
    )


@pytest.mark.django_db()
class TestFetchWidgetEmbedKey:
    def test_returns_the_channel_token(self, widget_channel):
        assert fetch_widget_embed_key(str(widget_channel.experiment.public_id)) == "a-widget-token"

    def test_ignores_deleted_channels(self, widget_channel):
        widget_channel.soft_delete()
        assert fetch_widget_embed_key(str(widget_channel.experiment.public_id)) == ""

    def test_ignores_other_platforms(self):
        channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.TELEGRAM, extra_data={"widget_token": "not-a-widget"}
        )
        assert fetch_widget_embed_key(str(channel.experiment.public_id)) == ""

    @pytest.mark.parametrize(
        "chatbot_id",
        [
            pytest.param("", id="empty"),
            pytest.param("not-a-uuid", id="not_a_uuid"),
            pytest.param("6d2f0b1e-0000-0000-0000-000000000000", id="unknown_chatbot"),
        ],
    )
    def test_returns_empty_string_without_a_channel(self, chatbot_id):
        assert fetch_widget_embed_key(chatbot_id) == ""


@pytest.mark.django_db()
class TestGetWidgetEmbedKey:
    @pytest.fixture(autouse=True)
    def _clear_cache(self, widget_channel):
        chatbot_id = str(widget_channel.experiment.public_id)
        clear_widget_embed_key_cache(chatbot_id)
        yield
        clear_widget_embed_key_cache(chatbot_id)

    def test_caches_the_token(self, widget_channel, django_assert_num_queries):
        chatbot_id = str(widget_channel.experiment.public_id)
        with django_assert_num_queries(1):
            assert get_widget_embed_key(chatbot_id) == "a-widget-token"
        with django_assert_num_queries(0):
            assert get_widget_embed_key(chatbot_id) == "a-widget-token"

    def test_caches_misses(self, django_assert_num_queries):
        chatbot_id = "6d2f0b1e-0000-0000-0000-000000000000"
        try:
            with django_assert_num_queries(1):
                assert get_widget_embed_key(chatbot_id) == ""
            with django_assert_num_queries(0):
                assert get_widget_embed_key(chatbot_id) == ""
        finally:
            clear_widget_embed_key_cache(chatbot_id)

    def test_clearing_the_cache_forces_a_refresh(self, widget_channel):
        chatbot_id = str(widget_channel.experiment.public_id)
        assert get_widget_embed_key(chatbot_id) == "a-widget-token"

        widget_channel.extra_data["widget_token"] = "rotated-token"
        widget_channel.save()
        assert get_widget_embed_key(chatbot_id) == "a-widget-token"

        clear_widget_embed_key_cache(chatbot_id)
        assert get_widget_embed_key(chatbot_id) == "rotated-token"

    def test_empty_chatbot_id_does_not_touch_the_cache(self):
        cache.set("WIDGET_EMBED_KEY:", "should-not-be-read")
        try:
            assert get_widget_embed_key("") == ""
        finally:
            cache.delete("WIDGET_EMBED_KEY:")
