from django import forms


class ConsentForm(forms.Form):
    email_address = forms.EmailField(required=False)
    consent_agreement = forms.BooleanField(required=True, label="I Agree")
    experiment_id = forms.IntegerField(widget=forms.HiddenInput())
    participant_id = forms.IntegerField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.initial.get("participant_id", None) and self.initial["email_address"]:
            # don't allow participants to change their email
            self.fields["email_address"].disabled = True


class SurveyForm(forms.Form):
    completed = forms.BooleanField(required=True, label="I have completed the survey.")


class ExperimentInvitationForm(forms.Form):
    experiment_id = forms.IntegerField(widget=forms.HiddenInput())
    email = forms.EmailField(required=True, label="Participant Email")
    invite_now = forms.BooleanField(label="Send Participant Invitation Immediately?", required=False)
