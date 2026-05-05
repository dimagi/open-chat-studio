import pytest
from django.test import override_settings

from apps.channels.forms import EmailChannelForm
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.utils.factories.experiment import ExperimentFactory


def _make_email_channel(team, experiment=None, email_address="bot@chat.openchatstudio.com", is_default=False):
    """Helper to create an email ExperimentChannel."""
    if experiment is None:
        experiment = ExperimentFactory(team=team)
    extra = {"email_address": email_address}
    if is_default:
        extra["is_default"] = True
    return ExperimentChannel.objects.create(
        team=team,
        experiment=experiment,
        platform=ChannelPlatform.EMAIL,
        extra_data=extra,
        name=f"email-{email_address}",
    )


@pytest.mark.django_db()
class TestEmailChannelForm:
    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_valid_form(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"email_address": "support@chat.openchatstudio.com", "platform": "email"},
        )
        assert form.is_valid(), form.errors

    def test_email_address_required(self, experiment):
        form = EmailChannelForm(experiment=experiment, data={"platform": "email"})
        assert not form.is_valid()
        assert "email_address" in form.errors

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_from_address_optional(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"email_address": "support@chat.openchatstudio.com", "platform": "email"},
        )
        assert form.is_valid()
        assert form.cleaned_data.get("from_address", "") == ""

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_is_default_defaults_to_false(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"email_address": "support@chat.openchatstudio.com", "platform": "email"},
        )
        assert form.is_valid()
        assert form.cleaned_data["is_default"] is False

    def test_duplicate_default_rejected(self, experiment):
        """Only one default email channel per team."""
        _make_email_channel(experiment.team, email_address="first@chat.openchatstudio.com", is_default=True)

        form = EmailChannelForm(
            experiment=experiment,
            data={
                "email_address": "second@chat.openchatstudio.com",
                "is_default": True,
                "platform": "email",
            },
        )
        assert not form.is_valid()
        assert "is_default" in form.errors

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_duplicate_default_allowed_when_editing_same_channel(self, experiment):
        """Editing the existing default channel should not trigger uniqueness error."""
        channel = _make_email_channel(
            experiment.team, experiment=experiment, email_address="first@chat.openchatstudio.com", is_default=True
        )

        form = EmailChannelForm(
            experiment=experiment,
            channel=channel,
            data={
                "email_address": "first@chat.openchatstudio.com",
                "is_default": True,
                "platform": "email",
            },
        )
        assert form.is_valid(), form.errors

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_email_address_on_allowed_domain_accepted(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"email_address": "support@chat.openchatstudio.com", "platform": "email"},
        )
        assert form.is_valid(), form.errors

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["*.openchatstudio.com"])
    def test_email_address_on_allowed_wildcard_accepted(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"email_address": "support@chat.openchatstudio.com", "platform": "email"},
        )
        assert form.is_valid(), form.errors

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_email_address_on_disallowed_domain_rejected(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"email_address": "support@evil.example.com", "platform": "email"},
        )
        assert not form.is_valid()
        assert "email_address" in form.errors
        # Error message should mention the allowed list so admins can self-serve.
        assert "Allowed: chat.openchatstudio.com" in str(form.errors["email_address"])

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=[])
    def test_email_address_rejected_when_setting_empty(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={"email_address": "support@chat.openchatstudio.com", "platform": "email"},
        )
        assert not form.is_valid()
        assert "email_address" in form.errors
        assert "no allowed domains" in str(form.errors["email_address"]).lower()

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_from_address_validated_when_set(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={
                "email_address": "support@chat.openchatstudio.com",
                "from_address": "noreply@evil.example.com",
                "platform": "email",
            },
        )
        assert not form.is_valid()
        assert "from_address" in form.errors

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com"])
    def test_from_address_skipped_when_blank(self, experiment):
        form = EmailChannelForm(
            experiment=experiment,
            data={
                "email_address": "support@chat.openchatstudio.com",
                "from_address": "",
                "platform": "email",
            },
        )
        assert form.is_valid(), form.errors

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=["chat.openchatstudio.com", "*.example.com"])
    def test_help_text_lists_allowed_domains(self, experiment):
        form = EmailChannelForm(experiment=experiment)
        help_text = form.fields["email_address"].help_text
        assert "Allowed domains: chat.openchatstudio.com, *.example.com." in help_text

    @override_settings(EMAIL_CHANNEL_ALLOWED_DOMAINS=[])
    def test_help_text_warns_when_no_domains_configured(self, experiment):
        form = EmailChannelForm(experiment=experiment)
        help_text = form.fields["email_address"].help_text
        assert "no allowed domains" in help_text.lower()
