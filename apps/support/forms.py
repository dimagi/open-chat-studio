from django import forms

from apps.users.models import CustomUser


class HijackUserForm(forms.Form):
    user_pk = forms.ModelChoiceField(queryset=CustomUser.objects.order_by("email"), label="User to impersonate")
