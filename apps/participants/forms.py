from django import forms

from apps.experiments.models import Participant


class ParticipantForm(forms.ModelForm):
    identifier = forms.CharField(disabled=True)
    public_id = forms.CharField(disabled=True)

    class Meta:
        model = Participant
        fields = ("identifier", "public_id", "user")


class ParticipantImportForm(forms.Form):
    file = forms.FileField()
