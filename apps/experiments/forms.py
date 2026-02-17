from django import forms
from django.core import validators
from django.utils.translation import gettext_lazy

from apps.experiments.models import (
    Survey,
)
from apps.service_providers.llm_service.default_models import get_default_translation_models_by_provider
from apps.service_providers.models import LlmProviderTypes
from apps.service_providers.utils import get_llm_provider_by_team, get_models_by_provider


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


class ExperimentVersionForm(forms.Form):
    version_description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    is_default_version = forms.BooleanField(required=False, label="Set as Published Version")

    class Meta:
        fields = ["version_description", "is_default_version"]
        help_texts = {"version_description": "A description of this version, or what changed from the previous version"}


class TranslateMessagesForm(forms.Form):
    target_language = forms.ChoiceField(
        choices=[],
        required=True,
        label="Select Language",
        widget=forms.Select(attrs={"class": "select select-bordered w-full", "id": "translation-language"}),
    )
    llm_provider = forms.ChoiceField(
        choices=[],
        required=True,
        label="Select LLM Provider",
        widget=forms.Select(attrs={"class": "select select-bordered w-full", "id": "translation-provider"}),
    )
    llm_provider_model = forms.ChoiceField(
        choices=[],
        required=True,
        label="Select LLM Model",
        widget=forms.Select(attrs={"class": "select select-bordered w-full", "id": "translation-provider-model"}),
    )

    def __init__(self, *args, team, translatable_languages, is_translate_all_form=False, **kwargs):
        super().__init__(*args, **kwargs)

        providers = get_llm_provider_by_team(team)
        provider_choices = [(provider.id, str(provider)) for provider in providers]

        self.fields["llm_provider"].choices = [("", "Choose a model for translation")] + provider_choices
        if provider_choices:
            self.fields["llm_provider"].choices = provider_choices
            self.fields["llm_provider"].initial = provider_choices[0][0]
            first_provider = providers[0]
            models_list = get_models_by_provider(first_provider.type, team)
            model_choices = [(model["value"], model["label"]) for model in models_list]
            self.fields["llm_provider_model"].choices = model_choices
            default_model_name_dict = get_default_translation_models_by_provider()
            default_model_name = default_model_name_dict.get(str(LlmProviderTypes[first_provider.type].label))
            default_model_value = next((value for value, label in model_choices if label == default_model_name), None)
            if default_model_value is not None:
                self.fields["llm_provider_model"].initial = default_model_value

        if is_translate_all_form:
            self.fields["llm_provider"].widget.attrs["id"] = "translation-provider-all"
            self.fields["llm_provider_model"].widget.attrs["id"] = "translation-provider-model-all"
        else:
            self.fields["llm_provider"].widget.attrs["id"] = "translation-provider-remaining"
            self.fields["llm_provider_model"].widget.attrs["id"] = "translation-provider-model-remaining"

        language_choices = [(code, name) for code, name in translatable_languages if code]
        if any(code == "eng" for code, _ in translatable_languages):
            self.fields["target_language"].choices = language_choices
            self.fields["target_language"].initial = "eng"
        else:
            self.fields["target_language"].choices = [("", "Choose a language")] + language_choices

        if is_translate_all_form:
            self.fields["target_language"].label = "Target Language for All Messages"
            self.fields["llm_provider"].label = "LLM Provider for Translation"
        else:
            self.fields["target_language"].label = "Target Language for Remaining Messages"
            self.fields["llm_provider"].label = "LLM Provider for Remaining Messages"
