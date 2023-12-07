from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.teams.backends import make_user_team_owner
from apps.teams.decorators import login_and_team_required
from apps.teams.forms import InvitationForm, TeamChangeForm
from apps.teams.invitations import send_invitation
from apps.teams.models import Invitation
from apps.teams.roles import is_admin
from apps.web.forms import set_form_fields_disabled


@login_required
def manage_teams(request):
    teams = request.user.teams.order_by("name")
    return render(
        request,
        "teams/list_teams.html",
        {
            "teams": teams,
            "page_title": _("Manage Teams"),
        },
    )


@login_and_team_required
def manage_team(request, team_slug):
    team = request.team
    team_form = None
    if request.method == "POST":
        if is_admin(request.user, team):
            team_form = TeamChangeForm(request.POST, instance=team)
            if team_form.is_valid():
                messages.success(request, _("Team details saved!"))
                team_form.save()
                if request.team.slug != team_slug:
                    return HttpResponseRedirect(reverse("single_team:manage_team", args=[request.team.slug]))
        else:
            messages.error(request, "Sorry you don't have permission to do that.")
    if team_form is None:
        team_form = TeamChangeForm(instance=team)
    if request.team_membership.is_team_admin:
        set_form_fields_disabled(team_form, True)

    return render(
        request,
        "teams/manage_team.html",
        {
            "team": team,
            "active_tab": "manage-team",
            "page_title": _("My Team | {team}").format(team=team),
            "team_form": team_form,
            "invitation_form": InvitationForm(team=request.team),
            "pending_invitations": Invitation.objects.filter(team=team, is_accepted=False).order_by("-created_at"),
        },
    )


@login_required
def create_team(request):
    if request.method == "POST":
        form = TeamChangeForm(request.POST)
        if form.is_valid():
            team = form.save()
            team.save()
            make_user_team_owner(team=team, user=request.user)
            return HttpResponseRedirect(reverse("teams:manage_teams"))
    else:
        form = TeamChangeForm()
    return render(
        request,
        "teams/manage_team.html",
        {
            "team_form": form,
            "create": True,
            "page_title": _("Create Team"),
        },
    )


@require_POST
@permission_required("teams.delete_team", raise_exception=True)
def delete_team(request, team_slug):
    request.team.delete()
    messages.success(request, _('The "{team}" team was successfully deleted').format(team=request.team.name))
    return HttpResponseRedirect(reverse("web:home"))


@require_POST
@permission_required("teams.change_invitation", raise_exception=True)
def resend_invitation(request, team_slug, invitation_id):
    invitation = get_object_or_404(Invitation, team=request.team, id=invitation_id)
    send_invitation(invitation)
    return HttpResponse('<span class="pg-button-light is-disbled btn-disabled">Sent!</span>')


@require_POST
@permission_required("teams.add_invitation", raise_exception=True)
def send_invitation_view(request, team_slug):
    form = InvitationForm(request.team, request.POST)
    if form.is_valid():
        invitation = form.save(commit=False)
        invitation.team = request.team
        invitation.invited_by = request.user
        try:
            # we have to do validation again on the model because the team wasn't set when form validation happened
            invitation.validate_unique()
        except ValidationError as e:
            form.add_error(None, e.messages[0])
        else:
            invitation.save()
            form.save_m2m()
            send_invitation(invitation)
            form = InvitationForm(request.team)  # clear saved data from the form
    else:
        pass
    return render(
        request,
        "teams/components/team_invitations.html",
        {
            "invitation_form": form,
            "pending_invitations": Invitation.objects.filter(team=request.team, is_accepted=False).order_by(
                "-created_at"
            ),
        },
    )


@require_POST
@permission_required("teams.delete_invitation", raise_exception=True)
def cancel_invitation_view(request, team_slug, invitation_id):
    invitation = get_object_or_404(Invitation, team=request.team, id=invitation_id)
    invitation.delete()
    return HttpResponse("")
