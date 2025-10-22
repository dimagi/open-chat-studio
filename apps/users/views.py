from allauth.account.utils import send_email_confirmation
from allauth.socialaccount.models import SocialAccount
from allauth_2fa.utils import user_has_valid_totp_device
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST
from django_htmx.http import HttpResponseLocation

from .forms import ApiKeyForm, CustomUserChangeForm, UploadAvatarForm
from .helpers import require_email_confirmation, user_has_confirmed_email_address
from .models import CustomUser

SESSION_API_KEY = "session_api_key"


@login_required
def profile(request):
    if request.method == "POST":
        form = CustomUserChangeForm(request.POST, instance=request.user)
        if form.is_valid():
            user = form.save(commit=False)
            user_before_update = CustomUser.objects.get(pk=user.pk)
            need_to_confirm_email = (
                user_before_update.email != user.email
                and require_email_confirmation()
                and not user_has_confirmed_email_address(user, user.email)
            )
            if need_to_confirm_email:
                # don't change it but instead send a confirmation email
                # email will be changed by signal when confirmed
                new_email = user.email
                send_email_confirmation(request, user, signup=False, email=new_email)
                user.email = user_before_update.email
                # recreate the form to avoid populating the previous email in the returned page
                form = CustomUserChangeForm(instance=user)
            user.save()

            user_language = user.language
            if user_language and user_language != translation.get_language():
                translation.activate(user_language)
            messages.success(request, _("Profile successfully saved."))
    else:
        form = CustomUserChangeForm(instance=request.user)

    new_api_key = request.session.pop(SESSION_API_KEY, None)
    return render(
        request,
        "account/profile.html",
        {
            "form": form,
            "active_tab": "profile",
            "page_title": _("Profile"),
            "api_keys": request.user.api_keys.filter(revoked=False).select_related("team"),
            "user_has_valid_totp_device": user_has_valid_totp_device(request.user),
            "new_api_key": new_api_key,
            "social_accounts": SocialAccount.objects.filter(user=request.user),
        },
    )


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
