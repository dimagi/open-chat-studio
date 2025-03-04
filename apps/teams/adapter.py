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
