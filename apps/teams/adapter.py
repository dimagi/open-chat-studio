from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.users.adapter import AccountAdapter

from .invitations import clear_invite_from_session


class AcceptInvitationAdapter(AccountAdapter):
    """
    Adapter that checks for an invitation id in the session and redirects
    to accepting it after login.

    Necessary to use team invitations with social login.
    """

    def get_login_redirect_url(self, request):
        from .models import Invitation

        if request.session.get("invitation_id"):
            invite_id = request.session.get("invitation_id")
            try:
                invite = Invitation.objects.get(id=invite_id)
                if not invite.is_accepted:
                    return reverse("teams:accept_invitation", args=[request.session["invitation_id"]])
                else:
                    clear_invite_from_session(request)
            except Invitation.DoesNotExist:
                pass
        return super().get_login_redirect_url(request)


class SsoAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        # Retrieve the initial email from the session
        initial_email = request.session.pop("initial_login_email", None)

        if initial_email:
            provider_email = sociallogin.user.email

            # Check if the provider's email matches the initial email
            if provider_email.lower() != initial_email.lower():
                raise PermissionDenied("The authenticated email does not match the one provided.")
