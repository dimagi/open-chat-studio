from django import forms
from django.core import validators
from django.utils.translation import gettext, gettext_lazy
from waffle import flag_is_active

from apps.custom_actions.form_utils import (
    clean_custom_action_operations,
    initialize_form_for_custom_actions,
    set_custom_actions,
)
from apps.experiments.models import (
    AgentTools,
    Experiment,
    ExperimentRoute,
    ExperimentRouteType,
    Survey,
    SyntheticVoice,
)
from apps.generics.help import render_help_with_link
from apps.utils.prompt import PromptVars, validate_prompt_variables


class ConsentForm(forms.Form):
    identifier = forms.CharField(required=False)
    consent_agreement = forms.BooleanField(required=True, label="I Agree")
    participant_id = forms.IntegerField(required=False, widget=forms.HiddenInput())

    def __init__(self, consent, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if consent.capture_identifier:
            self.fields["identifier"].required = True
            self.fields["identifier"].label = consent.identifier_label

            if consent.identifier_type == "email":
                self.fields["identifier"].widget = forms.EmailInput()
                self.fields["identifier"].validators = [validators.validate_email]

            if self.initial.get("participant_id", None) or self.initial.get("identifier", None):
                # don't allow participants to change their email
                self.fields["identifier"].disabled = True
        else:
            del self.fields["identifier"]


class SurveyCompletedForm(forms.Form):
    completed = forms.BooleanField(required=True, label="I have completed the survey.")


class SurveyForm(forms.ModelForm):
    class Meta:
        model = Survey
        fields = ["name", "url", "confirmation_text"]
        labels = {
            "confirmation_text": "User Message",
        }
        help_texts = {
            "url": gettext_lazy(
                "Use the {participant_id}, {session_id} and {experiment_id} variables if you want to "
                "include the participant, session and experiment session ids in the url."
            ),
            "confirmation_text": gettext_lazy(
                "The message that will be displayed to the participant to initiate the survey."
                " Use the <code>{survey_link}</code> tag to place the survey link in the text.<br/>"
                "If you want to use this survey in a web channel you can omit the <code>{survey_link}</code> tag"
                " as the link will be displayed below the text.<br/>"
                "If you want to use this survey in a non-web channel you should instruct the user"
                " to respond with '1' to indicate that they have completed the survey."
            ),
        }


class ExperimentInvitationForm(forms.Form):
    experiment_id = forms.IntegerField(widget=forms.HiddenInput())
    email = forms.EmailField(required=True, label="Participant Email")
    invite_now = forms.BooleanField(label="Send Participant Invitation Immediately?", required=False)


class ProcessorBotForm(forms.ModelForm):
    class Meta:
        model = ExperimentRoute
        fields = ["child", "keyword", "is_default"]


class TerminalBotForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        kwargs["initial"] = {**kwargs.get("initial", {}), "is_default": True}
        super().__init__(*args, **kwargs)

    class Meta:
        model = ExperimentRoute
        fields = ["child", "is_default"]
        labels = {"child": "Terminal bot"}
        widgets = {"is_default": forms.HiddenInput()}


EXPERIMENT_ROUTE_TYPE_FORMS = {
    ExperimentRouteType.PROCESSOR.value: ProcessorBotForm,
    ExperimentRouteType.TERMINAL.value: TerminalBotForm,
}


class ExperimentForm(forms.ModelForm):
    PROMPT_HELP_TEXT = """
        <p>Available variables to include in your prompt:</p>
        <p>{source_material}: Must be included when there is source material linked to the experiment.</p>
        <p>{participant_data}: Optional</p>
        <p>{current_datetime}: Only required when the bot is using a tool</p>
    """
    type = forms.ChoiceField(
        choices=[
            ("llm", gettext("Base Language Model")),
            ("assistant", gettext("OpenAI Assistant")),
        ],
        widget=forms.RadioSelect(attrs={"x-model": "type"}),
    )
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    prompt_text = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}), required=False)
    input_formatter = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    seed_message = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    tools = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, choices=AgentTools.choices, required=False)
    custom_action_operations = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, required=False)

    class Meta:
        model = Experiment
        fields = [
            "name",
            "description",
            "llm_provider",
            "llm_provider_model",
            "assistant",
            "pipeline",
            "temperature",
            "prompt_text",
            "input_formatter",
            "safety_layers",
            "conversational_consent_enabled",
            "source_material",
            "seed_message",
            "pre_survey",
            "post_survey",
            "consent_form",
            "voice_provider",
            "synthetic_voice",
            "safety_violation_notification_emails",
            "voice_response_behaviour",
            "tools",
            "echo_transcript",
            "use_processor_bot_voice",
            "trace_provider",
            "participant_allowlist",
            "debug_mode_enabled",
            "citations_enabled",
        ]
        labels = {"source_material": "Inline Source Material", "participant_allowlist": "Participant allowlist"}
        help_texts = {
            "source_material": "Use the '{source_material}' tag to inject source material directly into your prompt.",
            "assistant": "If you have an OpenAI assistant, you can select it here to use it for this experiment.",
            "use_processor_bot_voice": (
                "In a multi-bot setup, use the configured voice of the bot that generated the output. If it doesn't "
                "have one, the router bot's voice will be used."
            ),
            "participant_allowlist": (
                "Separate identifiers with a comma. Phone numbers should be in E164 format e.g. +27123456789"
            ),
            "debug_mode_enabled": (
                "Enabling this tags each AI message in the web UI with the bot responsible for generating it. "
                "This is applicable only for router bots."
            ),
            "citations_enabled": "Whether to include cited sources in responses",
        }

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        team = request.team
        exclude_services = [SyntheticVoice.OpenAIVoiceEngine]
        if flag_is_active(request, "open_ai_voice_engine"):
            exclude_services = []

        if flag_is_active(request, "pipelines-v2"):
            self.fields["type"].choices += [("pipeline", gettext("Pipeline"))]

        # Limit to team's data
        self.fields["llm_provider"].queryset = team.llmprovider_set
        self.fields["assistant"].queryset = team.openaiassistant_set.exclude(is_version=True)
        self.fields["pipeline"].queryset = team.pipeline_set.exclude(is_version=True).order_by("name")
        self.fields["voice_provider"].queryset = team.voiceprovider_set.exclude(
            syntheticvoice__service__in=exclude_services
        )
        self.fields["safety_layers"].queryset = team.safetylayer_set.exclude(is_version=True)
        self.fields["source_material"].queryset = team.sourcematerial_set.exclude(is_version=True)
        self.fields["pre_survey"].queryset = team.survey_set.exclude(is_version=True)
        self.fields["post_survey"].queryset = team.survey_set.exclude(is_version=True)
        self.fields["consent_form"].queryset = team.consentform_set.exclude(is_version=True)
        self.fields["synthetic_voice"].queryset = SyntheticVoice.get_for_team(team, exclude_services)
        self.fields["trace_provider"].queryset = team.traceprovider_set
        initialize_form_for_custom_actions(team, self)

        # Alpine.js bindings
        self.fields["voice_provider"].widget.attrs = {
            "x-model.fill": "voiceProvider",
        }
        self.fields["llm_provider"].widget.attrs = {
            "x-model.number.fill": "llmProviderId",
        }
        # special template for dynamic select options
        self.fields["synthetic_voice"].widget.template_name = "django/forms/widgets/select_dynamic.html"
        self.fields["llm_provider_model"].widget.template_name = "django/forms/widgets/select_dynamic.html"

        self.fields["prompt_text"].help_text = render_help_with_link(self.PROMPT_HELP_TEXT, "concepts.prompt_variables")
        self.fields["type"].help_text = render_help_with_link("", "concepts.experiments")

    def clean_participant_allowlist(self):
        cleaned_identifiers = []
        for identifier in self.cleaned_data["participant_allowlist"]:
            cleaned_identifiers.append(identifier.replace(" ", ""))
        return cleaned_identifiers

    def clean_custom_action_operations(self):
        return clean_custom_action_operations(self)

    def clean(self):
        cleaned_data = super().clean()

        errors = {}
        bot_type = cleaned_data["type"]
        if bot_type == "llm":
            cleaned_data["assistant"] = None
            cleaned_data["pipeline"] = None
            if not cleaned_data.get("prompt_text"):
                errors["prompt_text"] = "Prompt text is required unless you select an OpenAI Assistant"
            if not cleaned_data.get("llm_provider"):
                errors["llm_provider"] = "LLM Provider is required unless you select an OpenAI Assistant"
            if not cleaned_data.get("llm_provider_model"):
                errors["llm_provider_model"] = "LLM Model is required unless you select an OpenAI Assistant"
            if cleaned_data.get("llm_provider") and cleaned_data.get("llm_provider_model"):
                if not cleaned_data["llm_provider"].type == cleaned_data["llm_provider_model"].type:
                    errors[
                        "llm_provider_model"
                    ] = "You must select a provider model that is the same type as the provider"

        elif bot_type == "assistant":
            cleaned_data["pipeline"] = None
            if not cleaned_data.get("assistant"):
                errors["assistant"] = "Assistant is required when creating an assistant experiment"
        elif bot_type == "pipeline":
            cleaned_data["assistant"] = None
            if not cleaned_data.get("pipeline"):
                errors["pipeline"] = "Pipeline is required when creating a pipeline experiment"

        if cleaned_data["conversational_consent_enabled"] and not cleaned_data["consent_form"]:
            errors["consent_form"] = "Consent form is required when conversational consent is enabled"

        if errors:
            raise forms.ValidationError(errors)

        validate_prompt_variables(
            form_data=cleaned_data,
            prompt_key="prompt_text",
            known_vars=set(PromptVars.values),
        )
        return cleaned_data

    def save(self, commit=True):
        experiment = super().save(commit=False)
        experiment.team = self.request.team
        experiment.owner = self.request.user
        if commit:
            experiment.save()
            set_custom_actions(experiment, self.cleaned_data.get("custom_action_operations"))
            self.save_m2m()
        return experiment


class ExperimentVersionForm(forms.ModelForm):
    version_description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    is_default_version = forms.BooleanField(required=False, label="Set as Published Version")

    class Meta:
        model = Experiment
        fields = ["version_description", "is_default_version"]
        help_texts = {"version_description": "A description of this version, or what changed from the previous version"}
