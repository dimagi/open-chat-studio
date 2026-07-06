from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from allauth.mfa.utils import is_mfa_enabled
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST
from django_htmx.http import HttpResponseLocation

from apps.oauth.models import OAuth2AccessToken
from apps.ocs_notifications.forms import NotificationPreferencesForm
from apps.ocs_notifications.models import UserNotificationPreferences
from apps.ocs_notifications.utils import bust_unread_notification_cache
from apps.web.waf import WafRule, waf_allow

from .forms import ApiKeyForm, CustomUserChangeForm, UploadAvatarForm
from .helpers import require_email_confirmation, user_has_confirmed_email_address
from .models import CustomUser

SESSION_API_KEY = "session_api_key"


@login_required
def profile(request):
    if request.method == "POST":
        form = CustomUserChangeForm(request.POST, instance=request.user)
        if form.is_valid():
            form = _save_profile_form(request, form)
    else:
        form = CustomUserChangeForm(instance=request.user)

    new_api_key = request.session.pop(SESSION_API_KEY, None)

    # Get or create notification preferences
    preferences = UserNotificationPreferences.objects.get_or_create(user=request.user, team=request.team)[0]
    notification_preferences_form = NotificationPreferencesForm(instance=preferences)

    oauth_tokens = (
        OAuth2AccessToken.objects.filter(user=request.user).select_related("application").order_by("-created")
    )

    available_scopes = settings.OAUTH2_PROVIDER.get("SCOPES", {})
    for token in oauth_tokens:
        token.scope_list = []
        if token.scope:
            for scope in token.scope.split():
                token.scope_list.append(available_scopes.get(scope, ""))

    return render(
        request,
        "account/profile.html",
        {
            "form": form,
            "notification_preferences_form": notification_preferences_form,
            "active_tab": "profile",
            "page_title": _("Profile"),
            "api_keys": request.user.api_keys.filter(revoked=False).select_related("team"),
            "oauth_tokens": oauth_tokens,
            "user_has_mfa_enabled": is_mfa_enabled(request.user, types=[Authenticator.Type.TOTP]),
            "new_api_key": new_api_key,
            "social_accounts": SocialAccount.objects.filter(user=request.user),
        },
    )


def _save_profile_form(request, form):
    """Persist a valid profile form, handling email-change side effects.

    Returns the form to render. When an email confirmation is sent the form is recreated
    so the (reverted) previous email is shown rather than the unconfirmed one.
    """
    user = form.save(commit=False)
    previous_email = CustomUser.objects.get(pk=user.pk).email
    email_changed = previous_email != user.email
    confirmation_sent = email_changed and _send_email_confirmation_if_required(request, user, previous_email)
    if confirmation_sent:
        form = CustomUserChangeForm(instance=user)
    user.save()
    if email_changed and not confirmation_sent:
        _sync_primary_email_address(user)

    user_language = user.language
    if user_language and user_language != translation.get_language():
        translation.activate(user_language)
    messages.success(request, _("Profile successfully saved."))
    return form


def _send_email_confirmation_if_required(request, user, previous_email):
    """Send a confirmation email for a changed address when verification is required.

    When confirmation is needed, the user's email is reverted to ``previous_email`` (it is
    updated by the ``email_confirmed`` signal once confirmed) and ``True`` is returned.
    Returns ``False`` when no confirmation is required.
    """
    if not (require_email_confirmation() and not user_has_confirmed_email_address(user, user.email)):
        return False
    EmailAddress.objects.add_email(request, user, user.email, confirm=True)
    user.email = previous_email
    return True


def _sync_primary_email_address(user):
    """Keep allauth's ``EmailAddress`` records in sync after a profile email change.

    Ensures a row exists for the new email (created unverified if absent) and promotes it
    to primary only when it is already verified, so an unverified address is never made
    primary. See ``apps/users/signals.py`` for the equivalent behavior when a brand-new
    email is confirmed.
    """
    email_address, _created = EmailAddress.objects.get_or_create(
        user=user, email__iexact=user.email, defaults={"email": user.email}
    )
    if email_address.verified:
        email_address.set_as_primary()


@waf_allow(WafRule.SizeRestrictions_BODY)
@login_required
@require_POST
def upload_profile_image(request):
    user = request.user
    form = UploadAvatarForm(request.POST, request.FILES)
    if form.is_valid():
        user.avatar = request.FILES["avatar"]
        user.save()
    return HttpResponse(_("Success!"))


@login_required
@require_http_methods(["GET", "POST"])
def create_api_key(request):
    form = ApiKeyForm(request, request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            instance, key = form.save()
            request.session[SESSION_API_KEY] = key
            return HttpResponseLocation(reverse("users:user_profile"), status=201)

    return render(
        request,
        "account/components/api_key_form.html",
        context={
            "form": form,
        },
    )


@login_required
@require_POST
def revoke_api_key(request):
    key_id = request.POST.get("key_id")
    api_key = request.user.api_keys.get(id=key_id)
    api_key.revoked = True
    api_key.save()
    messages.success(
        request,
        _("API Key {key} has been revoked. It can no longer be used to access the site.").format(
            key=api_key.prefix,
        ),
    )
    return HttpResponseRedirect(reverse("users:user_profile"))


@login_required
@require_POST
def revoke_oauth_token(request):
    token_id = request.POST.get("token_id")
    token = get_object_or_404(OAuth2AccessToken, id=token_id, user=request.user)
    token.revoke()
    messages.success(
        request,
        _("OAuth access token for {app} has been revoked.").format(
            app=token.application.name,
        ),
    )
    return HttpResponseRedirect(reverse("users:user_profile"))


@login_required
@require_POST
def save_notification_preferences(request):
    """Save notification preferences from the profile page"""
    preferences = UserNotificationPreferences.objects.get_or_create(user=request.user, team=request.team)[0]
    form = NotificationPreferencesForm(request.POST, instance=preferences)
    if form.is_valid():
        form.save()
        bust_unread_notification_cache(request.user.id, team_slug=request.team.slug)
        messages.success(request, _("Notification preferences saved successfully."))
    else:
        messages.error(request, _("Failed to save notification preferences."))
    return HttpResponseRedirect(reverse("users:user_profile"))
