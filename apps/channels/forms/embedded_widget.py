import json
import secrets
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.postgres.forms import SimpleArrayField  # ty: ignore[unresolved-import]
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import format_html

from apps.channels import widget_versions
from apps.channels.forms.base import ExtraFormBase
from apps.channels.forms.widgets import CredentialModeSelect, WidgetParams
from apps.channels.models import CredentialMode, ExperimentChannel
from apps.channels.utils import ALL_DOMAINS, validate_domain_or_wildcard

# Floor for the per-channel session lifetime override: anything shorter would make every
# session on the channel dead on arrival.
MIN_SESSION_TOKEN_LIFETIME = timedelta(minutes=5)


class EmbeddedWidgetChannelForm(ExtraFormBase):
    credential_mode = forms.ChoiceField(
        label="Credential",
        choices=CredentialMode.choices,
        initial=CredentialMode.EMBED_KEY,
        # Not required, so a submission that omits it falls back to the mode already stored
        # rather than resetting the channel to the public default.
        required=False,
    )
    allow_all_domains = forms.BooleanField(
        label="Allow all domains", required=False, help_text="Allow access from any domain."
    )
    allowed_domains = SimpleArrayField(
        forms.CharField(
            max_length=100,
            validators=[validate_domain_or_wildcard],
        ),
        delimiter="\n",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "class": "textarea textarea-bordered w-full",
                "placeholder": "Enter one domain per line, e.g.:\nexample.com\nwww.mysite.org",
            }
        ),
        required=False,
        help_text=(
            "Enter the domains where this widget is allowed to be embedded (one per line). "
            "Required for an embed key. With OAuth, leaving this blank makes the channel "
            "server-only: any browser request is refused."
        ),
    )

    session_token_lifetime = forms.DurationField(
        label="Session lifetime",
        required=False,
        help_text=(
            "How long each session token stays usable from when it is issued. Activity does not extend it. "
            "Leave blank to use the system default. Format <code>HH:MM:SS</code>, or "
            "<code>D HH:MM:SS</code> for days — e.g. <code>12:00:00</code> or <code>2 00:00:00</code>."
        ),
        widget=forms.TextInput(attrs={"placeholder": "e.g. 12:00:00"}),
    )

    widget_token = forms.CharField(
        label="Widget Configuration",
        required=False,
        widget=forms.HiddenInput(),
        help_text="Configuration parameters for the widget",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `initial` is the channel's own extra_data dict (see ExperimentChannel.extra_form), so
        # seed a copy: the values this form puts there — a timedelta among them — must never
        # reach the JSON column.
        self.initial = dict(self.initial)
        # A real column rather than extra_data, so it round-trips through the instance
        # rather than through the config dict the other fields on this form become. Seeded
        # from the channel so a post_save that runs without clean() cannot silently clear it.
        self._session_token_lifetime = self.channel.session_token_lifetime if self.channel else None
        self._credential_mode = self.channel.credential_mode if self.channel else CredentialMode.EMBED_KEY
        self.fields["credential_mode"].help_text = self._credential_mode_help_text()
        self.fields["credential_mode"].widget = CredentialModeSelect(
            experiment=self.experiment, choices=CredentialMode.choices
        )
        if self.channel:
            self.initial["credential_mode"] = self.channel.credential_mode
            self.initial["session_token_lifetime"] = self.channel.session_token_lifetime
            allowed_domains = self.channel.extra_data.get("allowed_domains", [])
            self.initial["allowed_domains"] = [domain for domain in allowed_domains if domain != ALL_DOMAINS]
            if not self.is_bound:
                # only set this if the form is not bound to avoid overriding the value from request.POST
                self.initial["allow_all_domains"] = any(domain == ALL_DOMAINS for domain in allowed_domains)

            widget_token = self.channel.extra_data.get("widget_token")
            if widget_token:
                self.initial["widget_token"] = widget_token
                self.fields["widget_token"].widget = WidgetParams(
                    experiment=self.channel.experiment, widget_token=widget_token, channel=self.channel
                )

        self.form_attrs = {
            "x-data": json.dumps(
                {
                    "allowAllDomains": self.initial.get("allow_all_domains", False),
                    # Seeded from the same value the select renders as chosen -- `BoundField.value()`
                    # is what `Select.get_context` is handed -- so `x-model` binding to it on init is
                    # a no-op rather than a silent change of the admin's selection.
                    "credentialMode": str(self["credential_mode"].value() or self._credential_mode),
                }
            )
        }
        self.fields["credential_mode"].widget.attrs["x-model"] = "credentialMode"
        self.fields["allow_all_domains"].widget.attrs["x-model.boolean"] = "allowAllDomains"
        self.fields["allowed_domains"].widget.attrs[":disabled"] = "allowAllDomains === true"

    def _credential_mode_help_text(self):
        return format_html(
            "What kind of token the widget must use to start a chat session. <strong>Embed key</strong> admits any "
            "visitor on an allowed domain, as a public widget. <strong>OAuth token</strong> means your "
            "backend creates a short-lived <code>{scope}</code> token and hands it to the widget;"
            " any embed key is then ignored. The OAuth application must be <strong>client-credentials</strong> "
            "and must list this chatbot "
            '(<a href="{url}" class="link" target="_blank">manage OAuth applications</a>).',
            scope=settings.CHAT_API_SCOPE,
            url=reverse("oauth_apps:home", args=[self.experiment.team.slug]),
        )

    def _pop_credential_mode(self, cleaned_data) -> str:
        """Take the mode out of `cleaned_data`, which becomes the channel's extra_data.

        A real column of its own, for the same reason `session_token_lifetime` is one: it must
        not also land in the JSON blob. A missing value keeps the mode the channel already has.
        """
        return cleaned_data.pop("credential_mode", None) or self._credential_mode

    @staticmethod
    def _pop_session_token_lifetime(cleaned_data):
        """Take the lifetime out of `cleaned_data`, which becomes the channel's extra_data.

        It has a column of its own, so it must not also land in the JSON blob — the same
        reason `allow_all_domains` is popped.
        """
        lifetime = cleaned_data.pop("session_token_lifetime", None)
        if lifetime is not None and lifetime < MIN_SESSION_TOKEN_LIFETIME:
            # A lifetime this short makes every session on the channel dead on arrival.
            raise ValidationError({"session_token_lifetime": "The session lifetime must be at least 5 minutes."})
        return lifetime

    def clean(self):
        """Generate or preserve the widget token"""
        cleaned_data = super().clean()

        self._session_token_lifetime = self._pop_session_token_lifetime(cleaned_data)
        self._credential_mode = self._pop_credential_mode(cleaned_data)

        allow_all_domains = cleaned_data.pop("allow_all_domains", False)
        no_domains = not allow_all_domains and not cleaned_data.get("allowed_domains")
        if no_domains and self._credential_mode == CredentialMode.EMBED_KEY:
            # An embed key with no domain list would admit a stolen key from anywhere, so the
            # list is mandatory there. Under `oauth` a blank list is a real configuration: it
            # means server-only, and the token is what authorises the caller.
            raise ValidationError(
                {"allowed_domains": "You must specify at least one domain or select 'Allow all domains'."}
            )

        # If editing existing channel, preserve the token
        if self.channel and self.channel.extra_data.get("widget_token"):
            cleaned_data["widget_token"] = self.channel.extra_data["widget_token"]
        else:
            # Generate token here so it's available when check_usage_by_another_experiment is called
            cleaned_data["widget_token"] = secrets.token_urlsafe(24)

        if allow_all_domains:
            cleaned_data["allowed_domains"] = [ALL_DOMAINS]

        return cleaned_data

    def post_save(self, channel: ExperimentChannel):
        update_fields = []
        if channel.credential_mode != self._credential_mode:
            channel.credential_mode = self._credential_mode
            update_fields.append("credential_mode")
        if channel.session_token_lifetime != self._session_token_lifetime:
            channel.session_token_lifetime = self._session_token_lifetime
            update_fields.append("session_token_lifetime")
        if update_fields:
            channel.save(update_fields=update_fields)
        self.success_message = "Channel saved successfully"
        if warning := self._oauth_mode_warning(channel):
            self.warning_message = warning

    def _oauth_mode_warning(self, channel: ExperimentChannel) -> str:
        """Whether this channel's embed can actually present a token yet.

        MIN_OAUTH_WIDGET_VERSION is advisory — nothing rejects on it — so an embed too old to
        send one just fails admission with the door's deliberately opaque 401. Saying so here is
        the only place an admin finds out before their visitors do.
        """
        if self._credential_mode != CredentialMode.OAUTH:
            return ""
        if not self.cleaned_data.get("allowed_domains"):
            # Server-only: a blank domain list refuses every browser request outright, so there
            # is no embed for the floor to constrain and nothing for an admin to upgrade. Read
            # from cleaned_data rather than the channel: this is the list being saved.
            return ""
        version = channel.widget_version
        min_version = widget_versions.MIN_OAUTH_WIDGET_VERSION
        if not widget_versions.is_older_than(version, min_version):
            return ""
        reported = f"reported widget version {version}" if version else "not reported a widget version"
        return (
            f"This channel now requires an OAuth token. Your site has {reported}, and only "
            f"{min_version} or newer can send one — upgrade the embed and install an "
            "authTokenProvider on the element, or chats will fail."
        )
