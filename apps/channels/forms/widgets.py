from django import forms
from django.conf import settings
from django.contrib.postgres.forms import SimpleArrayField  # ty: ignore[unresolved-import]
from django.urls import reverse

from apps.channels.models import CredentialMode, ExperimentChannel
from apps.oauth.models import manage_applications_url
from apps.oauth.permissions import applications_allowing_chatbot


class WidgetParams(forms.Widget):
    template_name = "channels/widgets/widget_params.html"

    def __init__(self, experiment, widget_token, channel=None):
        super().__init__()
        self.experiment = experiment
        self.widget_token = widget_token
        self.channel = channel

    def format_value(self, value):
        return "" if value is None else value

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["experiment"] = self.experiment
        context["widget"]["token"] = self.widget_token
        if self.channel:
            context["widget"]["oauth"] = self.channel.credential_mode == CredentialMode.OAUTH
            context["widget"]["oauth_mode"] = CredentialMode.OAUTH.value
            context["widget"]["version"] = self.channel.widget_version
            context["widget"]["version_updated_at"] = self.channel.widget_version_updated_at
            context["widget"]["version_status"] = self.channel.widget_update_status
            context["widget"]["min_version"] = self.channel.min_widget_version
            context["widget"]["pending_min_version"] = self.channel.pending_min_widget_version
            context["widget"]["pending_effective_at"] = self.channel.pending_auth_level_effective_at
        context["docs_base_url"] = settings.DOCUMENTATION_BASE_URL
        context["docs_links"] = settings.DOCUMENTATION_LINKS
        return context


class CredentialModeSelect(forms.Select):
    """The credential mode select along with the list of oauth applications which can be used with
    this chatbot. If there are none a warning is displayed.
    """

    template_name = "channels/widgets/credential_mode.html"

    def __init__(self, experiment, attrs=None, choices=()):
        super().__init__(attrs, choices)
        self.experiment = experiment

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["oauth_mode"] = CredentialMode.OAUTH.value
        context["widget"]["oauth_applications"] = list(applications_allowing_chatbot(self.experiment))
        context["widget"]["manage_applications_url"] = manage_applications_url(self.experiment.team.slug)
        return context


class PublicLinkParams(forms.Widget):
    template_name = "channels/widgets/public_link.html"

    def __init__(self, channel: ExperimentChannel):
        super().__init__()
        self.channel = channel

    def format_value(self, value):
        return "" if value is None else value

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["public_url"] = self.channel.public_url
        context["widget"]["edit_url"] = reverse(
            "channels:channel_edit_dialog",
            args=[self.channel.team.slug, self.channel.experiment_id, self.channel.id],
        )
        return context


class LinesField(SimpleArrayField):
    """One entry per non-blank line; blank and trailing lines are dropped rather than rejected."""

    def to_python(self, value):
        lines = [line.strip() for line in (value or "").splitlines()]
        return super().to_python("\n".join(line for line in lines if line))
