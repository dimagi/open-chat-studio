import string
from unittest.mock import Mock

import pytest

from apps.channels.forms import (
    EmbeddedWidgetChannelForm,
)
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.utils import match_domain_pattern, validate_embedded_widget_request
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory


class TestEmbeddedWidgetChannelForm:
    def test_form_generates_token_for_new_channel(self):
        form = EmbeddedWidgetChannelForm(data={"allowed_domains": "example.com\n*.subdomain.com"}, experiment=Mock())

        assert form.is_valid()
        assert len(form.cleaned_data["widget_token"]) == 32
        assert form.cleaned_data["allowed_domains"] == ["example.com", "*.subdomain.com"]

    def test_form_preserves_token_for_existing_channel(self):
        existing_token = "existing_token_12345678901234567890"
        channel = Mock()
        channel.extra_data = {"widget_token": existing_token, "allowed_domains": ["example.com", "localhost:3000"]}

        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": "example.com\nlocalhost:3000"}, channel=channel, experiment=Mock()
        )

        assert form.is_valid()
        assert form.cleaned_data["widget_token"] == existing_token

    @pytest.mark.parametrize(
        ("domains_input", "is_valid", "expected_domains"),
        [
            ("example.com", True, ["example.com"]),
            ("example.com\n*.subdomain.com", True, ["example.com", "*.subdomain.com"]),
            ("localhost:3000\n127.0.0.1:8000", True, ["localhost:3000", "127.0.0.1:8000"]),
            ("", True, []),
            ("invalid..domain", False, None),  # Invalid domain format
            (
                "example.com\n*.subdomain.com\nlocalhost:3000",
                True,
                ["example.com", "*.subdomain.com", "localhost:3000"],
            ),
        ],
    )
    def test_domain_validation(self, domains_input, is_valid, expected_domains):
        form = EmbeddedWidgetChannelForm(data={"allowed_domains": domains_input}, experiment=Mock())

        assert form.is_valid() == is_valid

        if is_valid:
            assert form.cleaned_data["allowed_domains"] == expected_domains
        else:
            assert "allowed_domains" in form.errors

    def test_domain_validation_edge_cases(self):
        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": " example.com \n\n  *.subdomain.com  \n\nlocalhost:3000\n\n"}, experiment=Mock()
        )

        assert form.is_valid()
        assert form.cleaned_data["allowed_domains"] == ["example.com", "*.subdomain.com", "localhost:3000"]

    def test_post_save_message(self):
        channel = Mock()
        channel.extra_data = {}

        form = EmbeddedWidgetChannelForm(data={"allowed_domains": "example.com"}, experiment=Mock())

        # Must validate form before accessing cleaned_data
        assert form.is_valid()

        form.post_save(channel)

        assert "Embedded widget channel created successfully" in form.success_message
        assert form.cleaned_data["widget_token"] in form.success_message


class TestEmbeddedWidgetUtils:
    @pytest.mark.parametrize(
        ("origin_domain", "allowed_pattern", "should_match"),
        [
            ("example.com", "example.com", True),
            ("localhost:3000", "localhost:3000", True),
            ("api.example.com", "*.example.com", True),
            ("sub.domain.example.com", "*.example.com", True),
            ("example.com", "*.example.com", False),
            ("example.com:80", "example.com", True),  # Origin has port, pattern doesn't
            ("example.com", "example.com:80", False),  # Pattern has port, origin doesn't
            ("example.com:443", "example.com:443", True),  # Same ports
            ("example.com:3000", "example.com:8000", False),  # Different ports
            ("api.example.com:3000", "*.example.com:3000", True),
            ("api.example.com:8000", "*.example.com:3000", False),
            ("api.example.com", "*.example.com:3000", False),
            ("other.com", "example.com", False),
            ("malicious.com", "*.example.com", False),
            ("example.com.evil.com", "*.example.com", False),
        ],
    )
    def test_match_domain_pattern(self, origin_domain, allowed_pattern, should_match):
        result = match_domain_pattern(origin_domain, allowed_pattern)
        assert result == should_match

    @pytest.mark.django_db()
    def test_validate_embedded_widget_request_success(self):
        team = TeamWithUsersFactory()
        token = "test_token_123456789012345678901234"

        channel = ExperimentChannelFactory(
            team=team,
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            extra_data={"widget_token": token, "allowed_domains": ["example.com", "*.subdomain.com"]},
        )
        is_valid, returned_channel = validate_embedded_widget_request(token, "example.com", team)
        assert is_valid is True
        assert returned_channel == channel

    @pytest.mark.django_db()
    def test_validate_embedded_widget_request_failures(self):
        team = TeamWithUsersFactory()
        token = "test_token_123456789012345678901234"

        ExperimentChannelFactory(
            team=team,
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            extra_data={"widget_token": token, "allowed_domains": ["example.com"]},
        )

        is_valid, returned_channel = validate_embedded_widget_request("invalid_token", "example.com", team)
        assert is_valid is False
        assert returned_channel is None


@pytest.mark.django_db()
class TestEmbeddedWidgetChannelModel:
    def test_embedded_widget_platform_available_in_dropdown(self):
        team = TeamWithUsersFactory()
        available_platforms = ChannelPlatform.for_dropdown(used_platforms=set(), team=team)
        assert ChannelPlatform.EMBEDDED_WIDGET in available_platforms
        assert available_platforms[ChannelPlatform.EMBEDDED_WIDGET] is True

    def test_embedded_widget_channel_identifier_key(self):
        assert ChannelPlatform.EMBEDDED_WIDGET.channel_identifier_key == "widget_token"

    def test_embedded_widget_extra_form(self):
        form = ChannelPlatform.EMBEDDED_WIDGET.extra_form()
        assert isinstance(form, EmbeddedWidgetChannelForm)

    def test_create_embedded_widget_channel(self):
        experiment = ExperimentFactory()
        token = "test_token_123456789012345678901234"
        domains = ["example.com", "*.subdomain.com", "localhost:3000"]

        channel = ExperimentChannelFactory(
            experiment=experiment,
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            extra_data={"widget_token": token, "allowed_domains": domains},
        )

        assert channel.platform == ChannelPlatform.EMBEDDED_WIDGET
        assert channel.extra_data["widget_token"] == token
        assert channel.extra_data["allowed_domains"] == domains

    def test_channel_usage_check_with_embedded_widget(self):
        ExperimentChannelFactory(
            platform=ChannelPlatform.EMBEDDED_WIDGET, extra_data={"widget_token": "existing_token_123456789012345678"}
        )
        new_experiment = ExperimentFactory()

        # Should raise exception for duplicate token usage
        with pytest.raises(ChannelAlreadyUtilizedException):
            ExperimentChannel.check_usage_by_another_experiment(
                ChannelPlatform.EMBEDDED_WIDGET, "existing_token_123456789012345678", new_experiment
            )

    def test_platform_choices_include_embedded_widget(self):
        choices_dict = dict(ChannelPlatform.choices)
        assert "embedded_widget" in choices_dict
        assert choices_dict["embedded_widget"] == "Embedded Widget"


def test_form_token_generation_is_secure():
    mock_experiment = Mock()
    form1 = EmbeddedWidgetChannelForm(data={"allowed_domains": "example.com"}, experiment=mock_experiment)
    form2 = EmbeddedWidgetChannelForm(data={"allowed_domains": "example.com"}, experiment=mock_experiment)

    assert form1.is_valid()
    assert form2.is_valid()

    token1 = form1.cleaned_data["widget_token"]
    token2 = form2.cleaned_data["widget_token"]

    assert token1 != token2
    assert len(token1) == 32
    assert len(token2) == 32

    allowed_chars = string.ascii_letters + string.digits + "-_"
    assert all(c in allowed_chars for c in token1)
    assert all(c in allowed_chars for c in token2)


@pytest.mark.django_db()
def test_embedded_widget_integration_with_existing_channels():
    """Test that embedded widget channels work alongside existing channel types."""
    team = TeamWithUsersFactory()

    ExperimentChannelFactory(
        team=team, platform=ChannelPlatform.TELEGRAM, extra_data={"bot_token": "telegram_token_123"}
    )
    ExperimentChannelFactory(
        team=team,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"widget_token": "widget_token_123456789012345678901234", "allowed_domains": ["example.com"]},
    )
    assert ExperimentChannel.objects.filter(team=team).count() == 2
