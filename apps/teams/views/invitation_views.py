import uuid

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from ..invitations import clear_invite_from_session, process_invitation
from ..models import Invitation
from ..roles import is_member


def accept_invitation(request, invitation_id: uuid.UUID):
    invitation = get_object_or_404(Invitation, id=invitation_id)
    if not invitation.is_accepted:
        # set invitation in the session in case needed later - e.g. to redirect after login
        request.session["invitation_id"] = str(invitation_id)
    else:
        clear_invite_from_session(request)
    user_email_matches = False
    if request.user.is_authenticated:
        user_email_matches = request.user.email.lower() == invitation.email.lower()
    if user_email_matches and is_member(request.user, invitation.team):
        messages.info(
            request,
            _("It looks like you're already a member of {team}. You've been redirected.").format(
                team=invitation.team.name
            ),
        )
        return HttpResponseRedirect(reverse("web_team:home", args=[invitation.team.slug]))

    if request.method == "POST":
        # accept invitation workflow
        if not request.user.is_authenticated:
            messages.error(request, _("Please log in again to accept your invitation."))
            return HttpResponseRedirect(reverse(settings.LOGIN_URL))
        else:
            if invitation.is_accepted:
                messages.error(request, _("Sorry, it looks like that invitation link has expired."))
                return HttpResponseRedirect(reverse("web:home"))
            else:
                process_invitation(invitation, request.user)
                clear_invite_from_session(request)
                messages.success(request, _("You successfully joined {}").format(invitation.team.name))
                return HttpResponseRedirect(reverse("web_team:home", args=[invitation.team.slug]))

    return render(
        request,
        "teams/accept_invite.html",
        {
            "invitation": invitation,
            "invitation_url": reverse("teams:accept_invitation", args=[invitation_id]),
            "user_email_matches": user_email_matches,
        },
    )
