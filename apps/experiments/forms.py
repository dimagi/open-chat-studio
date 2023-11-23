from django import forms
from django.core import validators


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

            if self.initial.get("participant_id", None) and self.initial["identifier"]:
                # don't allow participants to change their email
                self.fields["identifier"].disabled = True
        else:
            del self.fields["identifier"]


class SurveyForm(forms.Form):
    completed = forms.BooleanField(required=True, label="I have completed the survey.")


class ExperimentInvitationForm(forms.Form):
    experiment_id = forms.IntegerField(widget=forms.HiddenInput())
    email = forms.EmailField(required=True, label="Participant Email")
    invite_now = forms.BooleanField(label="Send Participant Invitation Immediately?", required=False)
