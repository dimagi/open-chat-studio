from django import forms
from django.db import transaction

from apps.channels.models import ExperimentChannel
from apps.experiments.helpers import excluded_voice_services, normalize_participant_allowlist
from apps.experiments.models import ConsentForm, Experiment, SyntheticVoice
from apps.pipelines.models import Pipeline
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
    participant_allowlist = forms.CharField(widget=forms.HiddenInput(), required=False)

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
            "participant_allowlist",
            "seed_message",
            "file_uploads_enabled",
        ]
        labels = {
            "participant_allowlist": "Participant allowlist",
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
        self.fields["synthetic_voice"].queryset = SyntheticVoice.get_for_team(team, exclude_services)
        self.fields["trace_provider"].queryset = team.traceprovider_set
        self.fields["consent_form"].queryset = ConsentForm.objects.filter(team=team, is_version=False)
        self.fields["synthetic_voice"].widget.template_name = "django/forms/widgets/select_dynamic.html"  # ty: ignore[invalid-assignment]
        self.fields["voice_provider"].widget.attrs = {
            "x-model.fill": "voiceProvider",
        }

    def clean_participant_allowlist(self):
        return normalize_participant_allowlist(self.cleaned_data["participant_allowlist"].split(","))

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
    it. Disabled channels are excluded because `ad_hoc_bot_message` refuses to send on one --
    offering it would only ever be a broadcast that silently goes nowhere.
    """
    return experiment.experimentchannel_set.exclude(enabled=False)


class BroadcastChannelWidget(forms.CheckboxSelectMultiple):
    """Checkboxes tagged with their platform so the dialog can react to what is ticked.

    The `data-platform` attribute is what arms the WhatsApp template warning; `x-model` feeds
    the same selection to the send button.
    """

    def __init__(self, attrs=None):
        super().__init__(attrs={"x-model": "selectedChannels", **(attrs or {})})

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        # `value` is a ModelChoiceIteratorValue wrapping the channel's pk; the instance is on it.
        option["attrs"]["data-platform"] = value.instance.platform
        return option


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

    @property
    def eligible_channels(self):
        """The channels on offer. Empty means there is nothing to broadcast on, so no dialog."""
        return self.fields["channels"].queryset
