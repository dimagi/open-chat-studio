from django import forms

from apps.channels.forms.base import WebhookUrlFormBase


class FacebookChannelForm(WebhookUrlFormBase):
    page_id = forms.CharField(label="Page ID", max_length=100)
