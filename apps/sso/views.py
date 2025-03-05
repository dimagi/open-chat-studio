from allauth import app_settings
from allauth.account.forms import PasswordField
from allauth.account.views import LoginView
from allauth.socialaccount.models import SocialApp
from allauth.utils import get_form_class
from django import forms
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from waffle import flag_is_active


class BaseLoginForm(forms.Form):
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)


class EmailForm(BaseLoginForm):
    login = forms.EmailField(
        label=_("Email"),
        widget=forms.TextInput(
            attrs={
                "type": "email",
                "placeholder": _("Email address"),
                "autocomplete": "email",
            }
        ),
    )


class PasswordForm(BaseLoginForm):
    login = forms.EmailField(widget=forms.HiddenInput())
    password = PasswordField(label=_("Password"), autocomplete="current-password")


class CustomLoginView(LoginView):
    """This custom login form works as follows:

    1. User requests login page
      - Form is rendered without the password field
    2. User submits the form
      - We check if the email requires SSO auth and if so redirect
      - If not re-render the password field
    3. Validate the email and password for login
    """

    def get_form_class(self):
        if not flag_is_active(self.request, "sso_login"):
            return super().get_form_class()

        if self.request.method == "GET":
            return EmailForm
        elif self.request.method == "POST":
            if "password" not in self.request.POST:
                return EmailForm
            else:
                return get_form_class(app_settings.FORMS, "signup", self.form_class)

    def form_valid(self, form):
        if not flag_is_active(self.request, "sso_login"):
            return super().form_valid(form)

        app, email = self._get_social_app(form)
        if app:
            provider = app.get_provider(self.request)
            # Store email in session to validate later
            self.request.session["initial_login_email"] = email
            self.request.session.modified = True

            # Redirect to the provider's login URL
            return redirect(provider.get_login_url(self.request))

        if "password" in form.cleaned_data:
            return super().form_valid(form)

        # user submitted email and no sso app found so as for their password
        form = PasswordForm(initial={"login": form.cleaned_data["login"]})
        return self.render_to_response(self.get_context_data(form=form))

    def _get_social_app(self, form):
        email = form.cleaned_data["login"]
        domain = email.split("@")[-1].lower()  # Extract domain
        # Map domains to providers (e.g., Azure AD for 'clientdomain.com')
        app = SocialApp.objects.filter(settings__email_domains__contains=[domain]).first()
        return app, email
