from django import forms
from django.db import transaction
from django.utils.functional import cached_property

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.helpers import excluded_voice_services
from apps.experiments.models import ConsentForm, Experiment
from apps.pipelines.models import Pipeline
from apps.pipelines.nodes.node_metadata import get_speakable_voices
from apps.service_providers.messaging_service import MetaCloudAPIService
from apps.service_providers.utils import get_first_llm_provider_by_team, get_first_llm_provider_model


class ChatbotForm(forms.ModelForm):
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)

    class Meta:
        model = Experiment
        fields = [
            "name",
            "description",
        ]

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request

    @transaction.atomic()
    def save(self, commit=True):
        team_id = self.request.team.id
        llm_provider = get_first_llm_provider_by_team(team_id)
        llm_provider_model = None
        if llm_provider:
            llm_provider_model = get_first_llm_provider_model(llm_provider, team_id)
        pipeline = Pipeline.create_default_pipeline_with_name(
            self.request.team, self.cleaned_data["name"], llm_provider.id if llm_provider else None, llm_provider_model
        )
        experiment = super().save(commit=False)
        experiment.team = self.request.team
        experiment.owner = self.request.user
        experiment.pipeline = pipeline
        if commit:
            experiment.save()
            self.save_m2m()
        return experiment


class ChatbotSettingsForm(forms.ModelForm):
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    seed_message = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)

    class Meta:
        model = Experiment
        fields = [
            "name",
            "description",
            "voice_provider",
            "synthetic_voice",
            "voice_response_behaviour",
            "echo_transcript",
            "trace_provider",
            "debug_mode_enabled",
            "conversational_consent_enabled",
            "consent_form",
            "seed_message",
            "file_uploads_enabled",
        ]
        labels = {
            "voice_provider": "Speech Provider",
            "voice_response_behaviour": "Response Provider",
        }
        help_texts = {
            "debug_mode_enabled": (
                "Enabling this tags each AI message in the web UI with the bot responsible for generating it. "
                "This is applicable only for router bots."
            ),
        }

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        team = request.team
        # Shared with the write API so the two offer the same set (see excluded_voice_services).
        exclude_services = excluded_voice_services(request)
        self.fields["voice_provider"].queryset = team.voiceprovider_set.exclude(
            syntheticvoice__service__in=exclude_services
        )
        self.fields["synthetic_voice"].queryset = get_speakable_voices(
            team, voice_providers=self.fields["voice_provider"].queryset, exclude_services=exclude_services
        )
        self.fields["trace_provider"].queryset = team.traceprovider_set
        self.fields["consent_form"].queryset = ConsentForm.objects.filter(team=team, is_version=False)
        self.fields["synthetic_voice"].widget.template_name = "django/forms/widgets/select_dynamic.html"  # ty: ignore[invalid-assignment]
        self.fields["voice_provider"].widget.attrs = {
            "x-model.fill": "voiceProvider",
        }

    @transaction.atomic()
    def save(self, commit=True):
        experiment = super().save(commit=False)

        if commit:
            experiment.save()
            self.save_m2m()
        return experiment


class CopyChatbotForm(forms.Form):
    new_name = forms.CharField(
        max_length=255,
        required=True,
    )


def get_broadcast_channels(experiment: Experiment):
    """Every channel of this chatbot a broadcast can go out on.

    A broadcast runs through `ad_hoc_bot_message`, the same path as a scheduled message, so any
    channel that can carry one can carry the other. On the chat widget that means the message
    is written to the chat history and picked up by the widget's polling rather than pushed,
    which is how a scheduled message reaches a widget participant too.

    Nothing filters by platform. The API, web and evaluations channels belong to the team
    rather than a chatbot (`ExperimentChannel.objects.get_team_*_channel` leaves `experiment`
    null), so they are already absent from this set and their sessions are unreachable through
    it.

    Disabled channels are included so the dialog can show them greyed out. `ad_hoc_bot_message`
    refuses to send on one, so a broadcast there would go nowhere -- the widget makes them
    unpickable and `clean_channels` turns away anyone who submits one regardless.
    """
    return experiment.experimentchannel_set.select_related("messaging_provider")


class BroadcastChannelWidget(forms.CheckboxSelectMultiple):
    """Checkboxes tagged with everything the dialog reacts to when they are ticked.

    `data-platform` arms both WhatsApp warnings. `data-template-status` and `data-provider-url`
    are what the template warning reads: which of the ticked channels cannot be vouched for, and
    where to go to fix them. `x-model` feeds the same selection to the send button.

    A disabled channel is rendered but not selectable. The custom option template greys its
    label and explains why on hover -- `disabled` on its own only greys the box, leaving the
    platform name beside it looking pickable.
    """

    option_template_name = "chatbots/components/broadcast_channel_option.html"

    def __init__(self, attrs=None):
        super().__init__(attrs={"x-model": "selectedChannels", **(attrs or {})})

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        # `value` is a ModelChoiceIteratorValue wrapping the channel's pk; the instance is on it.
        channel = value.instance
        option["attrs"]["data-platform"] = channel.platform
        if channel.is_disabled:
            option["attrs"]["disabled"] = True
        if channel.platform == ChannelPlatform.WHATSAPP:
            provider = channel.messaging_provider
            option["attrs"]["data-template-status"] = whatsapp_template_status(provider)
            if provider:
                option["attrs"]["data-provider-url"] = provider.get_absolute_url()
        return option


def whatsapp_template_status(provider) -> str:
    """How the broadcast dialog should treat a WhatsApp channel's message template.

    Read straight off the provider's cache, so opening the dialog never calls Meta -- the
    provider page's refresh button is what fills it in.

    Only Meta Cloud API providers have a template Open Chat Studio can check, so a Twilio or
    Turn.io channel lands on "missing" along with a Meta provider nobody has checked yet. Both
    are the same thing to the sender: a broadcast we cannot promise will arrive.
    """
    if provider is None:
        return "missing"
    match provider.whatsapp_template_ok:
        case True:
            return "ok"
        case False:
            return "problem"
        case _:
            return "missing"


class BroadcastChannelField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        """The platform is the whole label.

        `ExperimentChannel.__str__` would add the channel's name, which defaults to the
        chatbot's -- the same on every row, so it only tells the reader which chatbot they are
        already looking at. A chatbot can hold only one channel per platform
        (`validate_platform_availability`), so the platform on its own is unambiguous.
        """
        return obj.platform_enum.label


class BroadcastMessageForm(forms.Form):
    """A one-off message sent to the recently active participants of a chatbot on the chosen channels."""

    # The limit is WhatsApp's: a broadcast lands outside the 24-hour service window, so it goes
    # out as a template message. Applied to every platform so the same text is deliverable on
    # all of the selected channels rather than being silently split on one of them.
    MESSAGE_CHAR_LIMIT = MetaCloudAPIService.TEMPLATE_MESSAGE_CHAR_LIMIT

    DEFAULT_ACTIVE_WITHIN_DAYS = 14
    MAX_ACTIVE_WITHIN_DAYS = 90

    channels = BroadcastChannelField(
        queryset=ExperimentChannel.objects.none(),
        widget=BroadcastChannelWidget,
        error_messages={"required": "Select at least one channel to broadcast on."},
    )
    active_within_days = forms.IntegerField(
        label="Active in the last (days)",
        initial=DEFAULT_ACTIVE_WITHIN_DAYS,
        min_value=1,
        max_value=MAX_ACTIVE_WITHIN_DAYS,
        help_text=(
            "Only participants whose last activity falls inside this many days are messaged. "
            "A participant who has not chatted since then is left alone."
        ),
    )
    message = forms.CharField(
        max_length=MESSAGE_CHAR_LIMIT,
        widget=forms.Textarea(attrs={"rows": 5, "x-model": "message"}),
    )

    def __init__(self, experiment: Experiment, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["channels"].queryset = get_broadcast_channels(experiment)

    def clean_channels(self):
        """Turn away a disabled channel even though it is in the queryset.

        The dialog renders it disabled, so a browser will not submit it -- but it is a valid
        choice as far as `ModelMultipleChoiceField` is concerned, and a broadcast on it would
        be dropped by `ad_hoc_bot_message` without the sender ever hearing about it.
        """
        channels = self.cleaned_data["channels"]
        disabled = [channel.platform_enum.label for channel in channels if channel.is_disabled]
        if disabled:
            raise forms.ValidationError(f"Cannot broadcast on a disabled channel: {', '.join(disabled)}.")
        return channels

    @cached_property
    def eligible_channels(self):
        """The channels a broadcast can actually go out on. Empty means no dialog.

        Disabled channels are listed in the dialog but cannot be picked, so they do not count
        towards whether the dialog is worth opening at all. Cached because the chatbot page asks
        twice: once for the button, once for the dialog.
        """
        return list(self.fields["channels"].queryset.filter(enabled=True))
