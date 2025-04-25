from allauth.account import app_settings
from allauth.account.forms import PasswordField
from allauth.account.views import LoginView, SignupView
from allauth.socialaccount.models import SocialApp
from allauth.utils import get_form_class
from django import forms
from django.contrib import messages
from django.contrib.auth.signals import user_logged_out
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from waffle import flag_is_active

from apps.sso.models import SsoSession
from apps.teams.models import Invitation


class BaseLoginForm(forms.Form):
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)


class LoginEmailForm(BaseLoginForm):
    login = forms.EmailField(
        label=_("Email"),
        widget=forms.TextInput(
            attrs={
                "type": "email",
                "placeholder": _("Email address"),
                "autocomplete": "email",
                "autofocus": True,
            }
        ),
    )


class LoginPasswordForm(BaseLoginForm):
    login = forms.EmailField(widget=forms.HiddenInput())
    password = PasswordField(
        label=_("Password"),
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                "placeholder": _("Password"),
                "autocomplete": "current-password",
                "autofocus": True,
            },
        ),
    )


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
            return LoginEmailForm
        elif self.request.method == "POST":
            if "password" not in self.request.POST:
                return LoginEmailForm
            else:
                return get_form_class(app_settings.FORMS, "login", self.form_class)

    def form_valid(self, form):
        if not flag_is_active(self.request, "sso_login"):
            return super().form_valid(form)

        if response := _redirect_for_sso(self.request, form.cleaned_data["login"]):
            return response

        if "password" in form.cleaned_data:
            return super().form_valid(form)

        # user submitted email and no sso app found so ask for their password
        form = LoginPasswordForm(initial={"login": form.cleaned_data["login"]})
        return self.render_to_response(self.get_context_data(form=form))


def _redirect_for_sso(request, email, for_signup=False):
    app, email = _get_social_app_for_email(email)
    if app:
        if email in app.settings.get("ignore_list", []):
            return
        if (allow_list := app.settings.get("allow_list")) and email not in allow_list:
            return

        provider = app.get_provider(request)
        # Store email in session to validate later
        request.session["initial_login_email"] = email
        request.session.modified = True

        # Redirect to the provider's login URL
        kwargs = {}
        if for_signup:
            kwargs["process"] = "signup"
        return redirect(provider.get_login_url(request, **kwargs))


def _get_social_app_for_email(email):
    domain = email.split("@")[-1].lower()  # Extract domain
    # Map domains to providers (e.g., Azure AD for 'clientdomain.com')
    app = SocialApp.objects.filter(settings__email_domains__contains=[domain]).first()
    return app, email


class SignupAfterInvite(SignupView):
    def get(self, request, *args, **kwargs):
        if self.invitation.is_accepted:
            messages.warning(
                self.request,
                _("The invitation has already been accepted. Please sign in to continue or request a new invitation."),
            )
            return redirect("web:home")

        if flag_is_active(self.request, "sso_login"):
            if response := _redirect_for_sso(self.request, self.invitation.email, for_signup=True):
                return response
        return super().get(request, *args, **kwargs)

    def is_open(self):
        """Allow signups from invitations even if public signups are closed."""
        return True

    @property
    def invitation(self) -> Invitation:
        invitation_id = self.kwargs["invitation_id"]
        return get_object_or_404(Invitation, id=invitation_id)

    def get_initial(self):
        initial = super().get_initial()
        if self.invitation:
            initial["team_name"] = self.invitation.team.name
            initial["email"] = self.invitation.email
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.invitation:
            context["invitation"] = self.invitation
        return context


def sso_logout(request):
    """
    This view is called by the SSO provider when the user logs out.
    See https://openid.net/specs/openid-connect-frontchannel-1_0.html
    See https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc#single-sign-out
    """
    sso_session_id = request.GET.get("sid")
    if not sso_session_id:
        return HttpResponse(status=200)

    sso_session = SsoSession.objects.select_related("user", "django_session").filter(id=sso_session_id).first()
    if not sso_session:
        return HttpResponse(status=200)

    session_matches_request = request.session.session_key == sso_session.django_session_id
    request.session.flush()
    if not session_matches_request:
        sso_session.django_session.delete()
        sso_session.delete()

    user_logged_out.send(sender=sso_session.user.__class__, request=request, user=sso_session.user)
    if hasattr(request, "user"):
        from django.contrib.auth.models import AnonymousUser

        request.user = AnonymousUser()

    return HttpResponse(status=200)
