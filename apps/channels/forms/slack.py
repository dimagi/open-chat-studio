import json
import re

from django import forms

from apps.channels.const import SLACK_ALL_CHANNELS
from apps.channels.exceptions import ExperimentChannelException
from apps.channels.forms.base import ExtraFormBase
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.slack_utils import normalize_slack_channel_name, resolve_slack_channel
from apps.experiments.exceptions import ChannelAlreadyUtilizedException

MAX_KEYWORDS = 5
MIN_KEYWORD_LENGTH = 2
MAX_KEYWORD_LENGTH = 25
KEYWORD_RE = re.compile(r"^[a-zA-Z0-9\-]+$")


def _validate_keyword(keyword: str):
    if len(keyword) > MAX_KEYWORD_LENGTH:
        raise forms.ValidationError(f"Keyword '{keyword}' is too long (maximum {MAX_KEYWORD_LENGTH} characters)")
    if len(keyword) < MIN_KEYWORD_LENGTH:
        raise forms.ValidationError(f"Keyword '{keyword}' is too short (minimum {MIN_KEYWORD_LENGTH} characters)")
    # No spaces, so a keyword is always a single word to match against
    if not KEYWORD_RE.match(keyword):
        raise forms.ValidationError(
            f"Keyword '{keyword}' contains invalid characters. Only letters, numbers, and hyphens are allowed."
        )


class SlackChannelForm(ExtraFormBase):
    """Slack messaging channels can be configured as follows (in increasing order of specificity):
    * scope: all, is_default: True, keywords: []
        * Will be the fallback handler if no other channels match. There can only be one per Slack workspace
    * scope: all, is_default: False, keywords: [...]
        * Will match messages from any channel based on the keywords. Keywords must be unique.
    * scope: <channel>, is_default: False, keywords: []
        * Will match all messages on the given channel, regardless of keywords.

    This mode is not currently supported:
    * scope: <channel>, is_default: False, keywords: [...]
    """

    channel_scope = forms.ChoiceField(
        label="Where should this bot operate?",
        choices=[
            ("specific", "Specific channel"),
            ("all", "All channels"),
        ],
        widget=forms.RadioSelect(attrs={"x-model": "channelScope"}),
    )
    routing_method = forms.ChoiceField(
        label="How should this bot receive messages?",
        choices=[
            ("keywords", "Respond to specific keywords"),
            ("default", "Default fallback (no matched keywords)"),
        ],
        widget=forms.RadioSelect(
            attrs={"x-model": "routingMethod", "control_attrs": {"x-show": "channelScope === 'all'"}}
        ),
        required=False,
    )
    slack_channel_name = forms.CharField(
        label="Channel Name",
        max_length=100,
        widget=forms.TextInput(attrs={"control_attrs": {"x-show": "channelScope === 'specific'"}}),
        required=False,
        help_text="Enter the channel name (e.g., general, support)",
    )
    slack_channel_id = forms.CharField(widget=forms.HiddenInput(), required=False)
    keywords = forms.CharField(
        label="Keywords",
        max_length=500,
        widget=forms.TextInput(
            attrs={
                "control_attrs": {"x-show": "routingMethod === 'keywords'"},
                "placeholder": "health, benefits, hr-support (comma-separated)",
            }
        ),
        required=False,
        help_text=(
            "Comma-separated keywords that will route messages to this bot when used as the first word after "
            "@mention (max 5 keywords, 25 chars each). Only letters, numbers, and hyphens allowed. "
            "Example: health, benefits, hr-support"
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._seed_initial_scope()

        # Set keywords field from extra_data
        if "keywords" in self.initial and isinstance(self.initial["keywords"], list):
            self.initial["keywords"] = ", ".join(self.initial["keywords"])

        self.form_attrs = {
            "x-data": json.dumps(
                {
                    "channelScope": self.initial.get("channel_scope", "specific"),
                    "routingMethod": self.initial.get("routing_method", "default"),
                }
            )
        }

    def _seed_initial_scope(self):
        if self.initial.get("slack_channel_id") != SLACK_ALL_CHANNELS:
            # routing_method is not used by specific channels
            self.initial["channel_scope"] = "specific"
            return

        self.initial["channel_scope"] = "all"
        self.initial["routing_method"] = self._initial_routing_method()

    def _initial_routing_method(self) -> str:
        if self.initial.get("is_default"):
            return "default"
        if self.initial.get("keywords"):
            return "keywords"
        return "default"

    def clean_slack_channel_name(self):
        return normalize_slack_channel_name(self.cleaned_data["slack_channel_name"])

    def clean_keywords(self):
        keywords_str = self.cleaned_data.get("keywords", "").strip()
        if not keywords_str:
            return []

        keywords = [kw.strip().lower() for kw in keywords_str.split(",") if kw.strip()]
        if len(keywords) > MAX_KEYWORDS:
            raise forms.ValidationError(f"Too many keywords (maximum {MAX_KEYWORDS} allowed)")

        for keyword in keywords:
            _validate_keyword(keyword)

        # dict keys deduplicate while preserving order
        return list(dict.fromkeys(keywords))

    def clean(self):
        cleaned_data = super().clean()
        if not self.messaging_provider:
            raise forms.ValidationError("Messaging provider is required.")

        channel_scope = cleaned_data.get("channel_scope")
        if channel_scope == "specific":
            self._clean_specific_channel(cleaned_data)
        elif channel_scope == "all":
            self._clean_all_channels(cleaned_data)

        return cleaned_data

    def _clean_specific_channel(self, cleaned_data):
        channel_name = cleaned_data.get("slack_channel_name", "").strip()
        if not channel_name:
            raise forms.ValidationError("Channel name is required for specific channels.")

        channel = resolve_slack_channel(self.messaging_provider, channel_name)
        if not channel:
            raise forms.ValidationError(f"No channel found with name {channel_name}")

        cleaned_data["slack_channel_id"] = channel["id"]
        # Specific channels don't use keywords or default routing
        cleaned_data["keywords"] = []
        cleaned_data["is_default"] = False
        self._validate_unique_channel(channel["id"])

    def _clean_all_channels(self, cleaned_data):
        cleaned_data["slack_channel_id"] = SLACK_ALL_CHANNELS
        cleaned_data["slack_channel_name"] = SLACK_ALL_CHANNELS

        routing_method = cleaned_data.get("routing_method")
        if routing_method == "keywords":
            self._clean_keyword_routing(cleaned_data)
        elif routing_method == "default":
            self._validate_unique_default()
            cleaned_data["keywords"] = []
            cleaned_data["is_default"] = True
        else:
            raise forms.ValidationError("Select a routing method for 'All channels' (keywords or default).")

    def _clean_keyword_routing(self, cleaned_data):
        keywords = cleaned_data.get("keywords", [])
        if not keywords:
            raise forms.ValidationError("Keywords are required when using keyword routing.")

        self._validate_unique_keywords(keywords)
        cleaned_data["is_default"] = False

    def _validate_unique_channel(self, slack_channel_id):
        queryset = self._get_channel_queryset().filter(extra_data__slack_channel_id=slack_channel_id)
        if existing_channel := self._filter_channels_by_slack_team(queryset):
            error_message = self._get_error_message(
                existing_channel,
                "This channel is already being used by another bot.",
                "This channel is already being used by {}",
            )
            raise forms.ValidationError({"slack_channel_name": error_message})

    def _filter_channels_by_slack_team(self, channels_queryset) -> ExperimentChannel | None:
        matching_channels = [
            channel for channel in channels_queryset.all() if self._channel_matches_slack_team(channel)
        ]
        return matching_channels[0] if matching_channels else None

    def _channel_matches_slack_team(self, channel) -> bool:
        # filtering must be done manually since the data is encrypted in the DB so can't be queried against
        if not self.messaging_provider:
            return False
        slack_team_id = self.messaging_provider.config.get("slack_team_id")
        if not slack_team_id:
            return False
        mp = channel.messaging_provider
        return mp is not None and mp.config.get("slack_team_id") == slack_team_id

    def _validate_unique_keywords(self, keywords):
        """Check that keywords are not already used by other channels system-wide"""
        keywords = {kw.lower() for kw in keywords}

        # Keywords must be unique across the entire Slack workspace
        queryset = self._get_channel_queryset().filter(
            extra_data__is_default=False,
            extra_data__slack_channel_id=SLACK_ALL_CHANNELS,
        )
        for channel in queryset:
            if conflicts := self._keyword_conflicts(channel, keywords):
                conflict_list = ", ".join(sorted(conflicts))
                error_message = self._get_error_message(
                    channel,
                    f"Some keywords already in use by another chatbot: {conflict_list}",
                    f"Some keywords are already used by {{}}: {conflict_list}",
                )
                raise forms.ValidationError({"keywords": error_message})

    def _keyword_conflicts(self, channel, keywords: set[str]) -> set[str]:
        """Which of `keywords` the channel already claims, matched case-insensitively."""
        if not self._channel_matches_slack_team(channel):
            return set()
        return keywords & {kw.lower() for kw in channel.extra_data.get("keywords", [])}

    def _validate_unique_default(self):
        """Check that there isn't already a default bot for this messaging provider"""
        # Default bots must be unique across the entire Slack workspace
        queryset = self._get_channel_queryset().filter(
            extra_data__is_default=True, extra_data__slack_channel_id=SLACK_ALL_CHANNELS
        )
        if existing_default := self._filter_channels_by_slack_team(queryset):
            suffix = " Please remove the default setting from that bot first."
            error_message = self._get_error_message(
                existing_default,
                f"There is already a default bot registered.{suffix}",
                f"There is already {{}} configured as the default bot.{suffix}",
            )
            raise forms.ValidationError({"routing_method": error_message})

    def _get_error_message(self, channel, other_team_message, this_team_message):
        if channel.team_id == self.experiment.team_id:
            return ChannelAlreadyUtilizedException.get_message_for_channel(channel, message_template=this_team_message)
        return other_team_message

    def _get_current_channel_id(self):
        if self.channel and self.channel.pk is not None:
            return self.channel.pk
        return None

    def _get_channel_queryset(self):
        queryset = ExperimentChannel.objects.filter(
            platform=ChannelPlatform.SLACK,
            deleted=False,
        ).select_related("experiment", "messaging_provider")
        if current_channel_id := self._get_current_channel_id():
            queryset = queryset.exclude(pk=current_channel_id)
        return queryset

    def post_save(self, channel: ExperimentChannel):
        channel_id = self.cleaned_data["slack_channel_id"]
        if channel_id != SLACK_ALL_CHANNELS and self.messaging_provider:
            service = self.messaging_provider.get_messaging_service()
            try:
                service.join_channel(channel_id)
            except Exception as e:
                raise ExperimentChannelException("Failed to join the channel") from e
