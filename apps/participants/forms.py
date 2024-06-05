from django import forms

from apps.experiments.models import Participant


class ParticipantForm(forms.ModelForm):
    class Meta:
        model = Participant
        fields = ["identifier", "public_id", "user"]
