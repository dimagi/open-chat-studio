from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.teams.decorators import login_and_team_required
from apps.teams.exceptions import TeamPermissionError
from apps.teams.forms import MembershipForm
from apps.teams.models import Membership
from apps.teams.roles import ROLE_ADMIN
from apps.web.forms import set_form_fields_disabled


@login_and_team_required
def team_membership_details(request, team_slug, membership_id):
    membership = get_object_or_404(Membership, team=request.team, pk=membership_id)
    editing_self = membership.user == request.user
    can_edit_team_members = request.team_membership.is_admin()
    if not can_edit_team_members and not editing_self:
        messages.error(request, _("Sorry, you don't have permission to access that page."))
        return HttpResponseRedirect(reverse("single_team:manage_team", args=[request.team.slug]))

    if request.method == "POST":
        # these conditions should not be possible in the UI, but we still need to check to prevent malicious behavior
        if not can_edit_team_members:
            raise TeamPermissionError(_("You don't have permission to edit team members in that team."))
        if editing_self:
            raise TeamPermissionError(_("You aren't allowed to change your own role."))

        membership_form = MembershipForm(request.POST, instance=membership)
        if membership_form.is_valid():
            membership = membership_form.save()
            messages.success(request, _("Role for {member} updated.").format(member=membership.user.get_display_name()))
    else:
        membership_form = MembershipForm(instance=membership)
    if editing_self:
        set_form_fields_disabled(membership_form)
    return render(
        request,
        "teams/team_membership_details.html",
        {
            "active_tab": "manage-team",
            "membership": membership,
            "membership_form": membership_form,
            "editing_self": editing_self,
        },
    )


@login_and_team_required
@require_POST
def remove_team_membership(request, team_slug, membership_id):
    membership = get_object_or_404(Membership, team=request.team, pk=membership_id)
    removing_self = membership.user == request.user
    can_edit_team_members = request.team_membership.is_admin()
    if not can_edit_team_members:
        if not removing_self:
            raise TeamPermissionError(_("You don't have permission to remove others from that team."))
    if membership.role == ROLE_ADMIN:
        admin_count = Membership.objects.filter(team=request.team, role=ROLE_ADMIN).count()
        if admin_count == 1:
            # trying to remove the last admin. this will get us in trouble.
            messages.error(
                request,
                _(
                    "You cannot remove the only administrator from a team. "
                    "Make another team member an administrator and try again."
                ),
            )
            return HttpResponseRedirect(reverse("single_team:manage_team", args=[request.team.slug]))

    membership.delete()
    messages.success(
        request,
        _("{member} was removed from {team}.").format(
            member=membership.user.get_display_name(), team=request.team.name
        ),
    )
    if removing_self:
        return HttpResponseRedirect(reverse("web:home"))
    else:
        return HttpResponseRedirect(reverse("single_team:manage_team", args=[request.team.slug]))
