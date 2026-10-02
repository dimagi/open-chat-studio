from django import forms
from django.core.exceptions import ValidationError

from apps.channels.forms.base import ExtraFormBase
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.utils import get_allowed_email_domains, is_email_domain_allowed


class EmailChannelForm(ExtraFormBase):
    email_address = forms.EmailField(
        label="Email Address",
        help_text=(
            "The email address that will receive messages for this channel (e.g., support@chat.openchatstudio.com)"
        ),
    )
    from_address = forms.EmailField(
        label="From Address",
        required=False,
        help_text="Optional: override the From address on outbound replies. Defaults to the system email.",
    )
    is_default = forms.BooleanField(
        label="Default fallback channel",
        required=False,
        help_text="When enabled, this channel receives emails that don't match any other email channel address.",
    )

    _NO_DOMAINS_HINT = "No allowed domains are currently configured. Contact your administrator before saving."
    _NO_DOMAINS_ERROR = "No allowed domains are currently configured. Contact your administrator."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = get_allowed_email_domains()
        if allowed:
            hint = f" Allowed domains: {', '.join(allowed)}."
        else:
            hint = f" {self._NO_DOMAINS_HINT}"
        self.fields["email_address"].help_text += hint

    def _validate_domain(self, address: str) -> str:
        if not is_email_domain_allowed(address):
            allowed = get_allowed_email_domains()
            if allowed:
                msg = f"Domain is not in the allowed list. Allowed: {', '.join(allowed)}."
            else:
                msg = self._NO_DOMAINS_ERROR
            raise ValidationError(msg)
        return address

    def clean_email_address(self):
        return self._validate_domain(self.cleaned_data["email_address"])

    def clean_from_address(self):
        value = self.cleaned_data.get("from_address", "")
        if not value:
            return value
        return self._validate_domain(value)

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("is_default"):
            existing = ExperimentChannel.objects.filter(
                platform=ChannelPlatform.EMAIL,
                extra_data__contains={"is_default": True},
                deleted=False,
            )
            if self.channel:
                existing = existing.exclude(pk=self.channel.pk)
            if existing.exists():
                self.add_error("is_default", "Another email channel is already set as the default.")
        return cleaned_data
