from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserChangeForm
from django.utils.translation import gettext

from ..api.models import UserAPIKey
from ..teams.models import Team
from .models import CustomUser


class CustomUserChangeForm(UserChangeForm):
    email = forms.EmailField(label=gettext("Email"), required=True)
    language = forms.ChoiceField(label=gettext("Language"))

    class Meta:
        model = CustomUser
        fields = ("email", "first_name", "last_name", "language")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if settings.USE_I18N and len(settings.LANGUAGES) > 1:
            language = self.fields.get("language")
            language.choices = settings.LANGUAGES
        else:
            self.fields.pop("language")


class UploadAvatarForm(forms.Form):
    avatar = forms.FileField()


class ApiKeyForm(forms.ModelForm):
    class Meta:
        model = UserAPIKey
        fields = ("team", "name")

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = request.user
        self.fields["team"].queryset = Team.objects.filter(membership__user=request.user)
        self.fields["team"].initial = request.team

    def save(self):
        instance = super().save(commit=False)
        instance.user = self.user
        key = UserAPIKey.objects.assign_key(instance)
        instance.save()
        return instance, key
