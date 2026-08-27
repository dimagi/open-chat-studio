from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from apps.channels.forms import (
    ChannelFormWrapper,
    EmbeddedWidgetChannelForm,
)
from apps.channels.models import ChannelPlatform, CredentialMode, ExperimentChannel, WidgetAuthLevel
from apps.channels.utils import match_domain_pattern
from apps.channels.widget_versions import MIN_OAUTH_WIDGET_VERSION
from apps.experiments.exceptions import ChannelAlreadyUtilizedException
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory


def _widget_channel(**kwargs):
    return ExperimentChannelFactory.create(
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"widget_token": "tok_123456789012345678901234", "allowed_domains": ["example.com"]},
        **kwargs,
    )


def _mock_experiment():
    """A stand-in experiment for the DB-less form tests.

    The form links to the team's OAuth applications, so it needs a reversible team slug --
    a bare Mock reverses to nothing.
    """
    return Mock(team=Mock(slug="test-team"))


class TestEmbeddedWidgetChannelForm:
    def test_form_generates_token_for_new_channel(self):
        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": "example.com\n*.subdomain.com"}, experiment=_mock_experiment()
        )

        assert form.is_valid()
        assert len(form.cleaned_data["widget_token"]) == 32
        assert form.cleaned_data["allowed_domains"] == ["example.com", "*.subdomain.com"]

    def test_form_preserves_token_for_existing_channel(self):
        existing_token = "existing_token_12345678901234567890"
        channel = Mock()
        channel.extra_data = {"widget_token": existing_token, "allowed_domains": ["example.com", "dimagi.com"]}

        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": "example.com\ndimagi.com"}, channel=channel, experiment=_mock_experiment()
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
        form = EmbeddedWidgetChannelForm(data={"allowed_domains": domains_input}, experiment=_mock_experiment())

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
            data={"allowed_domains": domains_input, "allow_all_domains": allow_all_input}, experiment=_mock_experiment()
        )
        assert form.is_valid()
        assert form.cleaned_data["allowed_domains"] == expected_domains

    def test_credential_mode_defaults_to_the_public_widget(self):
        form = EmbeddedWidgetChannelForm(data={"allowed_domains": "example.com"}, experiment=_mock_experiment())
        assert form.is_valid()
        assert form._credential_mode == CredentialMode.EMBED_KEY

    def test_the_credential_mode_help_text_links_to_the_teams_oauth_applications(self):
        """Choosing `oauth` means work a Chatbot Admin may not be able to do: the token's
        application has to list this chatbot, and that lives on the other side of the team."""
        form = EmbeddedWidgetChannelForm(experiment=_mock_experiment())
        help_text = form.fields["credential_mode"].help_text
        assert reverse("oauth_apps:home", args=["test-team"]) in help_text
        assert MIN_OAUTH_WIDGET_VERSION in help_text

    @pytest.mark.parametrize(
        ("mode", "is_valid"),
        [
            pytest.param(CredentialMode.EMBED_KEY, False, id="embed-key-needs-a-domain-list"),
            pytest.param(CredentialMode.OAUTH, True, id="oauth-may-be-server-only"),
        ],
    )
    def test_a_blank_domain_list_is_only_allowed_under_oauth(self, mode, is_valid):
        """A blank list means server-only, which is honest for a machine integration but would
        let a stolen embed key be used from anywhere."""
        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": "", "credential_mode": mode}, experiment=_mock_experiment()
        )
        assert form.is_valid() == is_valid
        if not is_valid:
            assert "allowed_domains" in form.errors

    @pytest.mark.django_db()
    def test_credential_mode_round_trips_without_leaking_into_extra_data(self):
        channel = _widget_channel()
        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": "example.com", "credential_mode": CredentialMode.OAUTH},
            channel=channel,
            experiment=channel.experiment,
        )
        assert form.is_valid()
        # cleaned_data becomes the channel's extra_data, and this one has a column of its own
        assert "credential_mode" not in form.cleaned_data

        form.post_save(channel)
        channel.refresh_from_db()
        assert channel.credential_mode == CredentialMode.OAUTH

        reloaded = EmbeddedWidgetChannelForm(channel=channel, experiment=channel.experiment)
        assert reloaded.initial["credential_mode"] == CredentialMode.OAUTH

    @pytest.mark.django_db()
    def test_switching_to_oauth_pins_the_auth_level(self):
        """An `oauth` channel below SESSION_TOKEN issues no session token and is dead on arrival,
        which the DB constraint forbids — so the partial save has to carry the pin."""
        channel = _widget_channel(required_auth_level=WidgetAuthLevel.EMBED_KEY)
        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": "example.com", "credential_mode": CredentialMode.OAUTH},
            channel=channel,
            experiment=channel.experiment,
        )
        assert form.is_valid()
        form.post_save(channel)
        channel.refresh_from_db()
        assert channel.required_auth_level == WidgetAuthLevel.SESSION_TOKEN
        assert channel.min_widget_version == MIN_OAUTH_WIDGET_VERSION

    @pytest.mark.django_db()
    def test_omitting_the_credential_mode_keeps_the_one_already_stored(self):
        """Omission must never relax the credential a channel demands."""
        channel = _widget_channel(credential_mode=CredentialMode.OAUTH)
        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": "example.com"}, channel=channel, experiment=channel.experiment
        )
        assert form.is_valid()
        form.post_save(channel)
        channel.refresh_from_db()
        assert channel.credential_mode == CredentialMode.OAUTH

    @pytest.mark.django_db()
    @pytest.mark.parametrize(
        ("widget_version", "warns"),
        [
            pytest.param(None, True, id="never-reported"),
            pytest.param("0.11.0", True, id="too-old-to-send-a-token"),
            pytest.param(MIN_OAUTH_WIDGET_VERSION, False, id="new-enough"),
        ],
    )
    def test_oauth_mode_warns_when_the_live_embed_cannot_send_a_token(self, widget_version, warns):
        """The version floor is advisory, so an old embed just fails admission with the door's
        deliberately opaque 401. This is where an admin finds out before their visitors do."""
        channel = _widget_channel(widget_version=widget_version)
        form = EmbeddedWidgetChannelForm(
            data={"allowed_domains": "example.com", "credential_mode": CredentialMode.OAUTH},
            channel=channel,
            experiment=channel.experiment,
        )
        assert form.is_valid()
        form.post_save(channel)
        assert bool(form.warning_message) == warns

    @pytest.mark.django_db()
    def test_creating_a_server_only_oauth_channel_through_the_wrapper(self):
        """The whole point of row 3, end to end: a channel an admin can actually create in
        `oauth` mode with no domain list, landing in the state the OAuth door needs — the mode
        set, the auth level pinned, and no lingering ratchet.
        """
        experiment = ExperimentFactory.create()
        wrapper = ChannelFormWrapper(
            experiment=experiment,
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            data={
                "platform": ChannelPlatform.EMBEDDED_WIDGET.value,
                "name": "oauth-widget",
                "enabled": "on",
                "allowed_domains": "",
                "credential_mode": CredentialMode.OAUTH,
            },
        )
        assert wrapper.is_valid(), (wrapper.channel_form.errors, wrapper.extra_form.errors)
        channel = wrapper.save()

        channel.refresh_from_db()
        assert channel.credential_mode == CredentialMode.OAUTH
        assert channel.required_auth_level == WidgetAuthLevel.SESSION_TOKEN
        assert channel.pending_auth_level is None
        assert channel.extra_data["allowed_domains"] == []

    @pytest.mark.django_db()
    def test_the_embed_snippet_carries_a_token_provider_only_in_oauth_mode(self):
        """A pure-snippet embed cannot use `oauth` mode — the provider is a JavaScript property
        with no attribute equivalent — so the token-minting requirement has to be visible here."""
        channel = _widget_channel(credential_mode=CredentialMode.OAUTH)
        snippet = render_to_string(
            "experiments/share/widget.html",
            {"experiment": channel.experiment, "embed_key": "tok_123456789012345678901234", "oauth": True},
        )
        assert "authTokenProvider" in snippet
        assert "embed-key" not in snippet
        # The dialog pastes this into a JS template literal.
        assert "`" not in snippet
        assert "${" not in snippet

        public = render_to_string(
            "experiments/share/widget.html",
            {"experiment": channel.experiment, "embed_key": "tok_123456789012345678901234", "oauth": False},
        )
        assert "authTokenProvider" not in public
        assert "embed-key" in public

    def test_required_auth_level_is_not_user_editable(self):
        """required_auth_level is a system-managed policy; it must not be exposed on the form."""
        form = EmbeddedWidgetChannelForm(data={"allowed_domains": "example.com"}, experiment=_mock_experiment())
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
            data={"allowed_domains": "example.com", "session_token_lifetime": lifetime}, experiment=_mock_experiment()
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
