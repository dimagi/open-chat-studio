from django import forms
from django.core import validators
from django.utils.translation import gettext_lazy

from apps.experiments.models import ExperimentRoute, ExperimentRouteType, Survey


class ConsentForm(forms.Form):
    identifier = forms.CharField(required=False)
    consent_agreement = forms.BooleanField(required=True, label="I Agree")
    experiment_id = forms.IntegerField(widget=forms.HiddenInput())
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
