from django import forms

from apps.channels.models import ExperimentChannel


class ChannelForm(forms.ModelForm):
    class Meta:
        model = ExperimentChannel
        fields = ["name", "platform"]
        widgets = {"platform": forms.HiddenInput()}

    def save(self, experiment, config_data: dict):
        self.instance.experiment = experiment
        self.instance.extra_data = config_data
        return super().save()


class TelegramChannelForm(forms.Form):
    bot_token = forms.CharField(label="Bot Token", max_length=100)


class WhatsappChannelForm(forms.Form):
    number = forms.CharField(label="Number", max_length=100)


class FacebookChannelForm(forms.Form):
    page_id = forms.CharField(label="Page ID", max_length=100)
    page_access_token = forms.CharField(label="Page Access Token")
    verify_token = forms.CharField(
        label="Verify Token", max_length=100, widget=forms.TextInput(attrs={"readonly": "readonly"})
    )
    webook_url = forms.CharField(
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
        label="Webhook URL",
        disabled=True,
        help_text="Use this as the webhook URL when setting up your Facebook App",
    )
