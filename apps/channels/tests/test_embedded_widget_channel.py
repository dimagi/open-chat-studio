from datetime import timedelta
from unittest.mock import Mock

import pytest

from apps.channels.forms import (
    EmbeddedWidgetChannelForm,
)
from apps.channels.models import ChannelPlatform, ExperimentChannel, WidgetAuthLevel
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

    def test_credential_mode_is_not_user_editable_yet(self):
        """Nothing resolves an OAuth token, so selecting that mode would strand the channel."""
        form = EmbeddedWidgetChannelForm(data={"allowed_domains": "example.com"}, experiment=Mock())
        assert "credential_mode" not in form.fields

    def test_required_auth_level_is_not_user_editable(self):
        """required_auth_level is a system-managed policy; it must not be exposed on the form."""
        form = EmbeddedWidgetChannelForm(data={"allowed_domains": "example.com"}, experiment=Mock())
        assert "required_auth_level" not in form.fields

    @pytest.mark.django_db()
    def test_user_cannot_override_required_auth_level_via_form(self):
        """A submitted required_auth_level is ignored; the channel keeps the model default."""
        channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            required_auth_level=WidgetAuthLevel.SESSION_TOKEN,
            extra_data={"widget_token": "tok_123456789012345678901234", "allowed_domains": ["example.com"]},
        )
        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": "example.com", "required_auth_level": WidgetAuthLevel.NONE.value},
            channel=channel,
            experiment=channel.experiment,
        )
        assert form.is_valid()
        assert "required_auth_level" not in form.cleaned_data
        form.post_save(channel)
        channel.refresh_from_db()
        assert channel.required_auth_level == WidgetAuthLevel.SESSION_TOKEN

    @pytest.mark.django_db()
    def test_session_token_lifetime_round_trips_without_leaking_into_extra_data(self):
        channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            extra_data={"widget_token": "tok_123456789012345678901234", "allowed_domains": ["example.com"]},
        )
        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": "example.com", "session_token_lifetime": "12:00:00"},
            channel=channel,
            experiment=channel.experiment,
        )
        assert form.is_valid()
        # cleaned_data becomes the channel's extra_data, and this one has a column of its own
        assert "session_token_lifetime" not in form.cleaned_data

        form.post_save(channel)
        channel.refresh_from_db()
        assert channel.session_token_lifetime == timedelta(hours=12)

        # An edit form on the saved channel shows the stored value back
        reloaded = EmbeddedWidgetChannelForm(channel=channel, experiment=channel.experiment)
        assert reloaded.initial["session_token_lifetime"] == timedelta(hours=12)

    @pytest.mark.parametrize(
        "lifetime",
        [
            pytest.param("00:01:00", id="under-the-floor"),
            pytest.param("-1 00:00:00", id="negative"),
        ],
    )
    def test_session_token_lifetime_floor(self, lifetime):
        """A lifetime this short would make every session on the channel dead on arrival."""
        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": "example.com", "session_token_lifetime": lifetime}, experiment=Mock()
        )
        assert not form.is_valid()
        assert "session_token_lifetime" in form.errors

    @pytest.mark.django_db()
    def test_building_the_form_does_not_pollute_extra_data(self):
        """`initial` is the channel's own extra_data dict, and a timedelta cannot go in a JSONField."""
        channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            session_token_lifetime=timedelta(hours=4),
            extra_data={"widget_token": "tok_123456789012345678901234", "allowed_domains": ["example.com"]},
        )
        channel.extra_form(experiment=channel.experiment)
        assert set(channel.extra_data) == {"widget_token", "allowed_domains"}
        channel.save()  # would raise TypeError if a timedelta had leaked in

    @pytest.mark.django_db()
    def test_blank_session_token_lifetime_clears_the_override(self):
        channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            session_token_lifetime=timedelta(hours=4),
            extra_data={"widget_token": "tok_123456789012345678901234", "allowed_domains": ["example.com"]},
        )
        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": "example.com", "session_token_lifetime": ""},
            channel=channel,
            experiment=channel.experiment,
        )
        assert form.is_valid()
        form.post_save(channel)
        channel.refresh_from_db()
        assert channel.session_token_lifetime is None


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
        ExperimentChannelFactory.create(
            platform=ChannelPlatform.EMBEDDED_WIDGET, extra_data={"widget_token": "existing_token_123456789012345678"}
        )
        new_experiment = ExperimentFactory.create()

        # Should raise exception for duplicate token usage
        with pytest.raises(ChannelAlreadyUtilizedException):
            ExperimentChannel.check_usage_by_another_experiment(
                ChannelPlatform.EMBEDDED_WIDGET, "existing_token_123456789012345678", new_experiment
            )
