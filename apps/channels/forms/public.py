import secrets

from django import forms
from django.contrib.postgres.forms import SimpleArrayField  # ty: ignore[unresolved-import]

from apps.channels.forms.base import ExtraFormBase
from apps.channels.forms.widgets import LinesField, PublicLinkParams
from apps.channels.models import ExperimentChannel


def _lines_field(label: str, help_text: str, placeholder: str) -> SimpleArrayField:
    return LinesField(
        forms.CharField(max_length=500),
        delimiter="\n",
        required=False,
        label=label,
        help_text=help_text,
        widget=forms.Textarea(
            attrs={"rows": 3, "class": "textarea textarea-bordered w-full", "placeholder": placeholder}
        ),
    )


class PublicChannelForm(ExtraFormBase):
    """Configuration for a public link. The link itself is the embed key, shown once a channel exists."""

    welcome_messages = _lines_field(
        "Welcome messages",
        "Shown above the composer before the visitor sends anything. One message per line.",
        "Hi! Ask me about opening hours or how to book.",
    )
    starter_questions = _lines_field(
        "Starter questions",
        "Buttons the visitor can tap to send a first message. One question per line.",
        "What are your opening hours?",
    )
    widget_token = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial = dict(self.initial)
        self._previous_token = self.channel.extra_data.get("widget_token") if self.channel else None
        self._previously_enabled = bool(self.channel and self.channel.enabled)
        if self._previous_token:
            self.initial["widget_token"] = self._previous_token
            self.fields["widget_token"].widget = PublicLinkParams(channel=self.channel)
            self.fields["widget_token"].label = ""

    def clean(self):
        cleaned_data = super().clean()
        cleaned_data.setdefault("welcome_messages", [])
        cleaned_data.setdefault("starter_questions", [])
        regenerate = self.data.get("regenerate_link") == "1"
        if self._previous_token and not regenerate:
            cleaned_data["widget_token"] = self._previous_token
        else:
            cleaned_data["widget_token"] = secrets.token_urlsafe(24)
        return cleaned_data

    def post_save(self, channel: ExperimentChannel):
        if self._previous_token and channel.extra_data.get("widget_token") != self._previous_token:
            ended = channel.end_live_sessions()
            self.success_message = (
                f"Link regenerated. The old link no longer works and {ended} live conversation(s) were ended."
            )
        elif self._previously_enabled and not channel.enabled:
            ended = channel.end_live_sessions()
            self.success_message = f"Channel disabled. {ended} live conversation(s) were ended."
        else:
            self.success_message = "Channel saved successfully"
