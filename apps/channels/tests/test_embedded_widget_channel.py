import string
from unittest.mock import Mock

import pytest

from apps.channels.forms import (
    EmbeddedWidgetChannelForm,
)
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.utils import match_domain_pattern
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
        channel.extra_data = {"widget_token": existing_token, "allowed_domains": ["example.com", "dimagi.com"]}

        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": "example.com\ndimagi.com"}, channel=channel, experiment=Mock()
        )

        assert form.is_valid()
        assert form.cleaned_data["widget_token"] == existing_token

    @pytest.mark.parametrize(
        ("domains_input", "is_valid", "expected_domains"),
        [
            ("example.com", True, ["example.com"]),
            ("example.com\n*.subdomain.com", True, ["example.com", "*.subdomain.com"]),
            ("invalid..domain", False, None),  # Invalid domain format
            ("", False, None),  # empty domain and not 'allow_all_domains'
        ],
    )
    def test_domain_validation(self, domains_input, is_valid, expected_domains):
        form = EmbeddedWidgetChannelForm(data={"allowed_domains": domains_input}, experiment=Mock())

        assert form.is_valid() == is_valid

        if is_valid:
            assert form.cleaned_data["allowed_domains"] == expected_domains
        else:
            assert "allowed_domains" in form.errors

    @pytest.mark.parametrize(
        ("domains_input", "allow_all_input", "expected_domains"),
        [
            ("", True, ["*"]),
            ("example.com\n*.subdomain.com", True, ["*"]),
            ("example.com", False, ["example.com"]),
        ],
    )
    def test_allow_all_domains(self, domains_input, allow_all_input, expected_domains):
        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": domains_input, "allow_all_domains": allow_all_input}, experiment=Mock()
        )
        assert form.is_valid()
        assert form.cleaned_data["allowed_domains"] == expected_domains


class TestEmbeddedWidgetUtils:
    @pytest.mark.parametrize(
        ("origin_domain", "allowed_pattern", "should_match"),
        [
            ("example.com", "example.com", True),
            ("api.example.com", "*.example.com", True),
            ("sub.domain.example.com", "*.example.com", True),
            ("example.com", "*.example.com", False),
            ("other.com", "example.com", False),
            ("malicious.com", "*.example.com", False),
            ("example.com.evil.com", "*.example.com", False),
        ],
    )
    def test_match_domain_pattern(self, origin_domain, allowed_pattern, should_match):
        result = match_domain_pattern(origin_domain, allowed_pattern)
        assert result == should_match


@pytest.mark.django_db()
class TestEmbeddedWidgetChannelModel:
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
