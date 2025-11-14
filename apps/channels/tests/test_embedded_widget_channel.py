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
